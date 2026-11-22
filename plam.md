# Django implementation plan: Natural-language Task API

**Assumptions.** Your brief's field table didn't come through, so I assumed the usual set: `title`, `description`, `completed`, `due_date`, `priority`, plus `parent` (self-FK), `recurrence`, and timestamps. If your table differs, only `models.py` and the serializer fields change.

## 1. Target layout

```
project/
├── config/              settings.py, urls.py
├── tasks/
│   ├── models.py
│   ├── serializers.py   TaskSerializer, strict fields, FromTextSerializer
│   ├── errors.py        error_response, errors→details, exception handler
│   ├── idempotency.py   in-memory store + @idempotent decorator
│   ├── ai.py            parse_task_text, break_down_task, optional LLM wrapper
│   ├── filters.py       TaskFilter
│   ├── pagination.py
│   ├── views.py         TaskViewSet
│   ├── urls.py
│   └── tests.py
├── docs/postman_collection.json
├── openapi.yaml
├── docker-compose.yml
├── requirements.txt
├── README.md
├── .env  (git-ignored)
└── .gitignore
```

## 2. Step 1: Setup

```bash
python -m venv venv && source venv/bin/activate
pip install django djangorestframework psycopg2-binary django-filter
django-admin startproject config .
python manage.py startapp tasks
pip freeze > requirements.txt
```

`docker-compose.yml`:
```yaml
services:
  db:
    image: postgres:16
    environment: { POSTGRES_DB: taskdb, POSTGRES_USER: taskuser, POSTGRES_PASSWORD: taskpass }
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
volumes: { pgdata: {} }
```

`config/settings.py` (key parts):
```python
import os
INSTALLED_APPS += ["rest_framework", "django_filters", "tasks"]

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.environ.get("DB_NAME", "taskdb"),
    "USER": os.environ.get("DB_USER", "taskuser"),
    "PASSWORD": os.environ.get("DB_PASSWORD", "taskpass"),
    "HOST": os.environ.get("DB_HOST", "localhost"),
    "PORT": os.environ.get("DB_PORT", "5432"),
}}

LLM_API_KEY = os.environ.get("LLM_API_KEY")          # None if missing
TASK_PARSER = "tasks.ai.parse_task_text"             # swap to ...parse_task_text_llm later

REST_FRAMEWORK = {
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "tasks.pagination.TaskPagination",
    "EXCEPTION_HANDLER": "tasks.errors.custom_exception_handler",
}
```

`.gitignore`: add `.env`, `venv/`, `__pycache__/`. Export `LLM_API_KEY` in your shell, or use `python-dotenv` if you prefer loading `.env`.

## 3. Step 2: Model, serializer, router

```python
# tasks/models.py
from django.db import models

class Task(models.Model):
    class Priority(models.TextChoices):
        LOW = "low"; MEDIUM = "medium"; HIGH = "high"
    class Recurrence(models.TextChoices):
        DAILY = "daily"; WEEKLY = "weekly"; MONTHLY = "monthly"; YEARLY = "yearly"

    title = models.CharField(max_length=200)
    description = models.TextField()
    completed = models.BooleanField(default=False)
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=6, choices=Priority.choices, default=Priority.MEDIUM)
    recurrence = models.CharField(max_length=10, choices=Recurrence.choices, blank=True, default="")
    parent = models.ForeignKey("self", null=True, blank=True,
                               on_delete=models.CASCADE, related_name="subtasks")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self): return self.title
```

```python
# tasks/urls.py
from rest_framework.routers import DefaultRouter
from .views import TaskViewSet
router = DefaultRouter()
router.register("tasks", TaskViewSet, basename="task")
urlpatterns = router.urls
# config/urls.py: path("api/", include("tasks.urls"))
```

Then run `makemigrations`, `migrate`, and test CRUD manually.

## 4. Step 3: Validation and the single error shape

**Serializer** with strict fields. DRF's defaults are too lenient: `BooleanField` accepts `"true"`/`1`, and `strptime` accepts `2026-2-3`.

```python
# tasks/serializers.py
import re
from rest_framework import serializers
from .models import Task

class StrictBooleanField(serializers.BooleanField):
    default_error_messages = {"invalid": "must be a boolean (true or false)"}
    def to_internal_value(self, data):
        if not isinstance(data, bool):
            self.fail("invalid")
        return data

class StrictDateField(serializers.DateField):
    default_error_messages = {"invalid": "must be a real date in YYYY-MM-DD format"}
    def to_internal_value(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            self.fail("invalid")
        return super().to_internal_value(value)   # rejects 2026-02-30

class TaskSerializer(serializers.ModelSerializer):
    completed = StrictBooleanField(required=False, default=False)
    due_date = StrictDateField(required=False, allow_null=True, input_formats=["%Y-%m-%d"])
    priority = serializers.ChoiceField(
        choices=Task.Priority.choices, required=False, default="medium",
        error_messages={"invalid_choice": "must be one of low, medium, high"})
    recurrence = serializers.ChoiceField(
        choices=Task.Recurrence.choices, required=False, allow_blank=True,
        error_messages={"invalid_choice": "must be one of daily, weekly, monthly, yearly"})
    parent = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Task
        fields = ["id", "title", "description", "completed", "due_date",
                  "priority", "recurrence", "parent", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

class FromTextSerializer(serializers.Serializer):
    text = serializers.CharField()   # blank/missing → standard error
```

`CharField` already trims whitespace and rejects blanks. Because `title` and `description` are non-blank model fields, no extra code is needed there.

**Error helpers:**

```python
# tasks/errors.py
from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler

ISSUE_MAP = {
    "This field may not be blank.": "must not be empty",
    "This field is required.": "is required",
    "This field may not be null.": "must not be null",
}

def error_response(code, message, details=None, status=400):
    return Response({"error": {"code": str(code), "message": str(message),
                               "details": details or []}}, status=status)

def errors_to_details(errors, prefix=""):
    """Flatten serializer.errors into [{'field','issue'}], all strings."""
    out = []
    if isinstance(errors, dict):
        for k, v in errors.items():
            out += errors_to_details(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(errors, list):
        for i, item in enumerate(errors):
            if isinstance(item, (dict, list)):
                out += errors_to_details(item, f"{prefix}[{i}]")
            else:
                s = str(item)
                out.append({"field": prefix or "non_field_errors",
                            "issue": ISSUE_MAP.get(s, s)})
    else:
        out.append({"field": prefix or "non_field_errors", "issue": str(errors)})
    return out

def validation_error_response(serializer, status=400, code="VALIDATION_ERROR",
                              message="Validation failed", prefix=""):
    return error_response(code, message, errors_to_details(serializer.errors, prefix), status)

def not_found(what="Task"):
    return error_response("NOT_FOUND", f"{what} not found", [], 404)

def custom_exception_handler(exc, context):
    """Safety net: bad JSON, invalid filter values, bad page numbers → same shape."""
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, exceptions.ValidationError):
        return error_response("VALIDATION_ERROR", "Validation failed",
                              errors_to_details(exc.detail), 400)
    if isinstance(exc, (exceptions.NotFound, Http404)):
        return error_response("NOT_FOUND", "Resource not found", [], 404)
    if isinstance(exc, exceptions.ParseError):
        return error_response("MALFORMED_REQUEST", "Malformed request body", [], 400)
    return error_response(getattr(exc, "default_code", "ERROR").upper(),
                          str(exc.detail) if hasattr(exc, "detail") else "Error",
                          [], response.status_code)
```

Also add a JSON `handler404` in `config/urls.py` so unknown URLs return your shape instead of Django's HTML page.

## 5. Steps 4–6: The viewset

```python
# tasks/views.py
from django.conf import settings
from django.db import transaction
from django.utils.module_loading import import_string
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Task
from .serializers import TaskSerializer, FromTextSerializer
from .errors import *
from .filters import TaskFilter
from .idempotency import idempotent
from .ai import break_down_task, LLMNotConfigured
from datetime import date

class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    filterset_class = TaskFilter
    search_fields = ["title", "description"]
    ordering_fields = ["due_date", "priority", "created_at", "title"]
    ordering = ["-created_at"]

    @staticmethod
    def _task_or_none(pk):
        try:
            return Task.objects.get(pk=pk)
        except (Task.DoesNotExist, ValueError, DjangoValidationError):
            return None

    # ---- CRUD ----
    @idempotent
    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        if not s.is_valid():
            return validation_error_response(s)
        s.save()
        return Response(s.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        task = self._task_or_none(kwargs["pk"])
        if task is None: return not_found()
        return Response(self.get_serializer(task).data)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)      # PATCH arrives with partial=True
        task = self._task_or_none(kwargs["pk"])
        if task is None: return not_found()
        s = self.get_serializer(task, data=request.data, partial=partial)
        if not s.is_valid(): return validation_error_response(s)
        s.save()
        return Response(s.data)

    def destroy(self, request, *args, **kwargs):
        task = self._task_or_none(kwargs["pk"])
        if task is None: return not_found()
        task.delete()                                 # cascades to subtasks
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---- Natural language ----
    @action(detail=False, methods=["post"], url_path="from-text")
    @idempotent
    def from_text(self, request):
        inp = FromTextSerializer(data=request.data)
        if not inp.is_valid():
            return validation_error_response(inp)
        try:
            parser = import_string(settings.TASK_PARSER)
            data = parser(inp.validated_data["text"], date.today())
        except LLMNotConfigured:
            return error_response("LLM_NOT_CONFIGURED", "LLM_API_KEY is not set", [], 503)
        except Exception:
            return error_response("PARSER_FAILED", "Could not parse the text", [], 502)
        s = TaskSerializer(data=data)                 # never trust the output
        if not s.is_valid():
            return validation_error_response(s, 502, "AI_OUTPUT_INVALID",
                                             "Parser returned invalid data")
        s.save()
        return Response(s.data, status=201)

    # ---- Subtasks ----
    @action(detail=True, methods=["get", "post"], url_path="subtasks")
    def subtasks(self, request, pk=None):
        task = self._task_or_none(pk)
        if task is None: return not_found()
        if request.method == "GET":
            return Response(TaskSerializer(task.subtasks.all(), many=True).data)
        if task.parent_id is not None:
            return error_response("SUBTASK_NOT_ALLOWED",
                                  "A subtask cannot have subtasks", [], 400)
        s = TaskSerializer(data=request.data)
        if not s.is_valid(): return validation_error_response(s)
        s.save(parent=task)
        return Response(s.data, status=201)

    @action(detail=True, methods=["post"], url_path="breakdown")
    @idempotent
    def breakdown(self, request, pk=None):
        task = self._task_or_none(pk)
        if task is None: return not_found()
        if task.parent_id is not None:
            return error_response("SUBTASK_NOT_ALLOWED",
                                  "A subtask cannot be broken down", [], 400)
        try:
            items = break_down_task(task)
        except Exception:
            return error_response("BREAKDOWN_FAILED", "Could not break down task", [], 502)
        if not isinstance(items, list) or not items:
            return error_response("AI_OUTPUT_INVALID", "Breakdown returned no items", [], 502)

        serializers_, details = [], []
        for i, item in enumerate(items):
            s = TaskSerializer(data=item)
            if not s.is_valid():
                details += errors_to_details(s.errors, f"items[{i}]")
            serializers_.append(s)
        if details:
            return error_response("AI_OUTPUT_INVALID", "Breakdown produced invalid items",
                                  details, 502)
        with transaction.atomic():                    # all-or-nothing
            for s in serializers_:
                s.save(parent=task)
        return Response([s.data for s in serializers_], status=201)
```

**Design notes**
- The helpers plus overridden methods meet the brief, and the exception handler also covers errors you don't control (invalid JSON, `?completed=maybe`, `?page=999`).
- Breakdown validation failure returns 502 because the bad data comes from the generator, not the client. The brief doesn't specify a status here, so 400 is also defensible. State your choice in the README.

## 6. Step 4: `tasks/ai.py`

```python
import re
from calendar import monthrange
from datetime import date, timedelta
from django.conf import settings

class LLMNotConfigured(Exception): pass

WEEKDAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
RECURRENCE = [
    (r"\bevery\s+day\b|\bdaily\b", "daily"),
    (r"\bevery\s+week\b|\bweekly\b", "weekly"),
    (r"\bevery\s+month\b|\bmonthly\b", "monthly"),
    (r"\bevery\s+(year|yr)\b|\byearly\b|\bannually\b", "yearly"),
]
LEAD_IN = r"^(please\s+)?(remind me to|remind me|i need to|i have to|don't forget to|todo:?)\s+"
ORDINAL = r"(?:\bon\s+)?(?:\bthe\s+)?\b(\d{1,2})(?:st|nd|rd|th)\b(?:\s+of)?"

def _next_day_of_month(today: date, day: int) -> date | None:
    y, m = today.year, today.month
    for _ in range(14):
        if day <= monthrange(y, m)[1]:
            d = date(y, m, day)
            if d >= today: return d
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return None

def parse_task_text(text: str, today: date) -> dict:
    """text + today -> {"title","description","due_date"(YYYY-MM-DD str),"recurrence"?}"""
    s = text.strip()
    low = s.lower()
    result = {"description": s}
    recurrence = None
    for pat, val in RECURRENCE:
        if re.search(pat, low):
            recurrence = val
            low = re.sub(pat, " ", low)
            break
    due = None
    if (m := re.search(r"\b\d{4}-\d{2}-\d{2}\b", low)):
        due = m.group(0)                      # left raw: serializer rejects 2026-02-30
        low = low.replace(due, " ")
    elif "day after tomorrow" in low:
        due = (today + timedelta(days=2)).isoformat(); low = low.replace("day after tomorrow", " ")
    elif "tomorrow" in low:
        due = (today + timedelta(days=1)).isoformat(); low = low.replace("tomorrow", " ")
    elif re.search(r"\btoday\b", low):
        due = today.isoformat(); low = re.sub(r"\btoday\b", " ", low)
    elif (m := re.search(ORDINAL, low)):
        d = _next_day_of_month(today, int(m.group(1)))
        due = d.isoformat() if d else None
        low = re.sub(ORDINAL, " ", low, count=1)
    else:
        for i, name in enumerate(WEEKDAYS):
            if re.search(rf"\b(next\s+|on\s+)?{name}\b", low):
                ahead = (i - today.weekday()) % 7 or 7
                due = (today + timedelta(days=ahead)).isoformat()
                low = re.sub(rf"\b(next\s+|on\s+)?{name}\b", " ", low); break

    title = re.sub(LEAD_IN, "", re.sub(r"\s+", " ", low).strip()).strip(" ,.-")
    result["title"] = title[:1].upper() + title[1:] if title else ""
    if due: result["due_date"] = due
    if recurrence: result["recurrence"] = recurrence
    return result
```

For `"Remind me to pay rent on the 1st of every month"`, the result is title "Pay rent", due date as the next 1st, recurrence "monthly", and description holding the original text. If cleanup leaves an empty title, the serializer rejects it and the endpoint returns 502. That is the designed behavior.

```python
def break_down_task(task) -> list[dict]:
    parts = [p.strip(" .") for p in re.split(r",|\band\b", task.title) if p.strip(" .")]
    if len(parts) < 2:
        parts = [f"Plan: {task.title}", f"Do: {task.title}", f"Review: {task.title}"]
    return [{"title": p[:1].upper() + p[1:], "description": f"Part of: {task.title}",
             "priority": task.priority,
             **({"due_date": task.due_date.isoformat()} if task.due_date else {})}
            for p in parts]

# Optional, same signature, selected via settings.TASK_PARSER
def parse_task_text_llm(text: str, today: date) -> dict:
    if not settings.LLM_API_KEY:
        raise LLMNotConfigured()
    # call the provider, ask for JSON with the same four keys, json.loads the reply
    raise NotImplementedError
```

Swapping the parser is a one-line settings change because the view loads it via `import_string(settings.TASK_PARSER)`.

## 7. Step 6: Idempotency

```python
# tasks/idempotency.py
import copy, json, threading
from functools import wraps
from rest_framework.response import Response
from .errors import error_response

_store: dict = {}                 # {scoped_key: {"body": str, "response": {...}}}
_lock = threading.RLock()

def clear_store(): _store.clear()           # used by tests

def idempotent(view):
    @wraps(view)
    def wrapper(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if not key:
            return view(self, request, *args, **kwargs)
        scoped = f"{request.method}:{request.path}:{key}"
        body = json.dumps(request.data, sort_keys=True, default=str)
        with _lock:                          # also blocks concurrent duplicates
            entry = _store.get(scoped)
            if entry:
                if entry["body"] != body:
                    return error_response(
                        "IDEMPOTENCY_KEY_REUSED",
                        "Idempotency-Key was already used with a different request body",
                        [{"field": "Idempotency-Key", "issue": "reused with a different body"}], 422)
                r = Response(copy.deepcopy(entry["response"]["data"]),
                             status=entry["response"]["status"])
                r["Idempotent-Replayed"] = "true"
                return r
            response = view(self, request, *args, **kwargs)
            if 200 <= response.status_code < 300:     # only successes are stored
                _store[scoped] = {"body": body, "response": {
                    "data": copy.deepcopy(response.data), "status": response.status_code}}
            return response
    return wrapper
```

- Decorator order matters: `@action(...)` on top, `@idempotent` below.
- On replay the view never runs, so the parser isn't called again.
- Keys are scoped by method and path, so the same key on `/from-text/` and `/breakdown/` can't collide.
- Document the limitation: the store is per-process and lost on restart, so production would use Redis or a DB table with a unique key.

## 8. Step 7: Filtering, search, ordering, pagination

```python
# tasks/filters.py
import django_filters as df
from .models import Task

class TaskFilter(df.FilterSet):
    due_before = df.DateFilter(field_name="due_date", lookup_expr="lte")
    due_after = df.DateFilter(field_name="due_date", lookup_expr="gte")
    is_subtask = df.BooleanFilter(field_name="parent", lookup_expr="isnull", exclude=True)
    class Meta:
        model = Task
        fields = {"completed": ["exact"], "priority": ["exact"], "parent": ["exact"]}

# tasks/pagination.py
from rest_framework.pagination import PageNumberPagination
class TaskPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100
```

Combined request to prove it works:
```
GET /api/tasks/?completed=false&priority=high&due_before=2026-12-31&search=rent&ordering=due_date&page=1&page_size=5
```

Alphabetical `ordering=priority` gives high, low, medium. If you want true ordering, annotate a `priority_rank` with `Case/When` in `get_queryset` and expose it in `ordering_fields`. Otherwise just note the caveat in the README.

## 9. Step 8: Testing and hardening

Write `tasks/tests.py` with `APITestCase`, calling `clear_store()` in `setUp`. Minimum cases:

| Area | Cases |
|---|---|
| CRUD | create/list/retrieve/PUT/PATCH/delete happy paths; 404 on unknown id and on `/api/tasks/abc/` |
| Validation | empty and whitespace title, empty description, `completed: "yes"`, `due_date: "2026-02-30"` and `"2026-2-3"`, `priority: "urgent"`, malformed JSON; assert the exact error shape and that `code`/`message` are strings |
| from-text | tomorrow, "1st of every month", `"text": ""`, missing `text`; text containing `2026-02-30` gives 502 and no row |
| Bad parser output | `mock.patch` the parser to return an empty title, a wrong type, and a raised exception; each gives 502 and `Task.objects.count()` unchanged |
| LLM key | `override_settings(TASK_PARSER=...llm, LLM_API_KEY=None)` gives 503 |
| Subtasks | create gives 201; list; subtask of a subtask gives 400; breakdown of a subtask gives 400; cascade delete |
| Breakdown | split on "and"/commas; template fallback; one invalid item means zero rows saved |
| Idempotency | same key + body gives the same response and one row; same key + different body gives 422; failed request, then a fixed retry with the same key succeeds; parser mock called once; no header gives two rows |
| Query | filter, search, ordering and pagination in one request; `?page=999` gives 404 in your shape; `?completed=maybe` gives 400 |

**AI workflow to document:**
1. Prompt an assistant for a test checklist and Postman requests.
2. Run all of them, with debugger breakpoints in `parse_task_text` and `break_down_task`.
3. Critique the checklist against the table above. Typical misses: whitespace-only strings, non-numeric ids, failed-then-retried keys, wrong JSON types, an empty-list breakdown, and key reuse across endpoints.
4. Add the missing requests and export `docs/postman_collection.json`.
5. Simulate bad model output by temporarily returning `title: ""` from the parser, then confirm 502 and no new row.

## 10. Step 9: Documentation

**README sections:**
1. Overview
2. Setup: venv, `pip install -r requirements.txt`, `docker compose up -d`, `migrate`, `runserver`, `export LLM_API_KEY=...` (optional)
3. Endpoint reference, each with a request and response: CRUD, `from-text`, subtasks (GET/POST), `breakdown`, a filtering example, and a validation error
4. Error codes table: `VALIDATION_ERROR` 400, `NOT_FOUND` 404, `SUBTASK_NOT_ALLOWED` 400, `IDEMPOTENCY_KEY_REUSED` 422, `PARSER_FAILED`/`AI_OUTPUT_INVALID` 502, `LLM_NOT_CONFIGURED` 503
5. Parser contract: `parse_task_text(text: str, today: date) -> dict` with its keys and the "output is revalidated" rule
6. Idempotency behavior and its limitation
7. What the AI checklist missed and what you changed

**`openapi.yaml`** (hand-written):
- `openapi: 3.0.3` with `info` and `servers`
- Paths: `/api/tasks/`, `/api/tasks/{id}/`, `/api/tasks/from-text/`, `/api/tasks/{id}/subtasks/`, `/api/tasks/{id}/breakdown/`
- `components/schemas`: `Task`, `TaskInput`, `FromTextRequest`, `PaginatedTasks`, `Error`, `ErrorDetail`
- `components/parameters`: `IdempotencyKey` (header), `page`, `page_size`, `search`, `ordering`, `completed`, `priority`, and the due-date filters
- Shared `responses` (`ValidationError`, `NotFound`, `BadGateway`, `ServiceUnavailable`, `UnprocessableEntity`) all referencing the `Error` schema

## 11. DB proof (for your submission)

In your DB client, run `SELECT id, title FROM tasks_task;`.

- **Idempotency:** send `POST /api/tasks/` twice with the same `Idempotency-Key`, then check there is exactly one row. Repeat for `from-text`, and for `breakdown` (the same N subtasks, not 2N).
- **Cascade:** delete a parent, then run `SELECT * FROM tasks_task WHERE parent_id = <id>;` and expect zero rows.
- **502 path:** with the empty-title hack, check that the row count is unchanged.

## 12. Suggested build order

1. Setup, model, and CRUD (Steps 1–2)
2. `errors.py` and the serializer, then override the CRUD methods (Step 3)
3. `ai.py` and `from-text` (Step 4)
4. Subtasks and breakdown (Step 5)
5. `idempotency.py` and decorating the three endpoints (Step 6)
6. Filters and pagination (Step 7)
7. Tests, Postman collection, and hardening (Step 8)
8. README and `openapi.yaml` (Step 9)

**Common pitfalls**
- Forgetting `@idempotent` under `@action`, or putting it above.
- Letting DRF's lenient `BooleanField` and `DateField` accept bad input.
- `ErrorDetail` objects leaking into the response instead of plain strings (`str()` them).
- Returning `PUT` validation errors in DRF's default shape because you only overrode `create`.
- Committing `.env`, or crashing at startup when `LLM_API_KEY` is unset (use `os.environ.get`).
- Caching error responses in the idempotency store.

If you want, I can write out the full `tests.py` or the complete `openapi.yaml` next.