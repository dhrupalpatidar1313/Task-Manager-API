# Natural-Language Task Manager API

A production-ready Django REST Framework API for task management featuring natural language task parsing, automatic subtask breakdown, strict type/date validation, uniform error shapes, and safe request idempotency.

---

## 1. Overview

The Task Manager API enables users and client applications to manage hierarchical tasks and subtasks efficiently. Features include:
- **Natural Language Parsing**: Turn natural input like *"Remind me to pay rent on the 1st of every month"* into structured task records with recurrence and calculated due dates.
- **Strict Validation**: Rejection of loose inputs (e.g., `"yes"` or `1` for booleans; non-existent calendar dates like `2026-02-30`; non-standard date formats like `2026-2-3`).
- **Single Predictable Error Shape**: All errors (validation, 404, parse errors, unhandled exceptions) conform to:
  ```json
  {
    "error": {
      "code": "STRING_CODE",
      "message": "Human readable message",
      "details": [
        {"field": "field_name", "issue": "specific reason"}
      ]
    }
  }
  ```
- **Idempotency Protection**: Safe POST retries via a database-backed `Idempotency-Key` store, with payload mismatch detection (HTTP 422).
- **Subtask Hierarchy**: Clean single-level subtasks with cascade deletion on parent destruction and automated breakdown of compound tasks.
- **Filtering, Search & Pagination**: Full query support via `django-filter`, DRF `SearchFilter`, and `OrderingFilter`.

---

## 2. Setup & Installation

### Prerequisites
- Python 3.10+ (or Python 3.12 / 3.14)
- PostgreSQL 16+ or Docker

### Step 1: Clone and Virtual Environment
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Database Setup
Using Docker Compose:
```bash
docker compose up -d
```
Or use an existing PostgreSQL instance configured in `.env`.

### Step 3: Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Default `.env` settings:
```ini
DB_ENGINE=django.db.backends.postgresql
DB_NAME=taskdb
DB_USER=taskuser
DB_PASSWORD=taskpass
DB_HOST=localhost
DB_PORT=5432
SECRET_KEY=your-secret-key
DEBUG=True
LLM_API_KEY=
TASK_PARSER=tasks.ai.parse_task_text
```
*(To run with SQLite for lightweight local testing or offline development, set `USE_SQLITE=True` or `DB_ENGINE=django.db.backends.sqlite3`)*

### Step 4: Run Migrations & Start Server
```bash
python manage.py makemigrations
python manage.py migrate
python manage.py runserver
```
The API is available at `http://localhost:8000/api/tasks/`.

---

## 3. Endpoint Reference

### 3.0 Authentication

Register with `POST /api/auth/register/` using `{"username":"...", "password":"at-least-8-characters"}`. Obtain an access and refresh token with `POST /api/auth/token/` using username and password, then send `Authorization: Bearer <access>` on task requests. Refresh an access token with `POST /api/auth/token/refresh/` and `{"refresh":"<refresh-token>"}`. All task endpoints require authentication and each account can access only its own tasks. Registration is public.

### 3.1 Standard CRUD (`/api/tasks/`)

#### Create Task
- **`POST /api/tasks/`**
- **Header (Optional):** `Idempotency-Key: <unique-uuid-or-token>`
- **Request:**
  ```json
  {
    "title": "Complete quarterly review",
    "description": "Compile OKR metrics and team feedback",
    "completed": false,
    "due_date": "2026-12-01",
    "priority": "high",
    "recurrence": "monthly"
  }
  ```
- **Response (`201 Created`):**
  ```json
  {
    "id": 1,
    "owner": 1,
    "title": "Complete quarterly review",
    "description": "Compile OKR metrics and team feedback",
    "completed": false,
    "due_date": "2026-12-01",
    "priority": "high",
    "recurrence": "monthly",
    "parent": null,
    "created_at": "2026-11-21T15:00:00Z",
    "updated_at": "2026-11-21T15:00:00Z"
  }
  ```

#### List Tasks
- **`GET /api/tasks/?completed=false&priority=high&due_before=2026-12-31&search=quarterly&ordering=due_date&page=1&page_size=10`**
- **Response (`200 OK`):**
  ```json
  {
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
      {
        "id": 1,
        "owner": 1,
        "title": "Complete quarterly review",
        "description": "Compile OKR metrics and team feedback",
        "completed": false,
        "due_date": "2026-12-01",
        "priority": "high",
        "recurrence": "monthly",
        "parent": null,
        "created_at": "2026-11-21T15:00:00Z",
        "updated_at": "2026-11-21T15:00:00Z"
      }
    ]
  }
  ```

#### Retrieve Task
- **`GET /api/tasks/1/`**
- **Response (`200 OK`):** Task JSON representation.

#### Update Task (PUT / PATCH)
- **`PUT /api/tasks/1/`**: Full replacement of task fields.
- **`PATCH /api/tasks/1/`**: Partial update (e.g. `{"completed": true}`).

#### Delete Task
- **`DELETE /api/tasks/1/`**
- **Response (`204 No Content`)**: Deletes task and cascades to child subtasks.

---

### 3.2 Natural Language Task Parsing (`/api/tasks/from-text/`)

- **`POST /api/tasks/from-text/`**
- **Header (Optional):** `Idempotency-Key: <unique-uuid>`
- **Request:**
  ```json
  {
    "text": "Remind me to pay rent on the 1st of every month"
  }
  ```
- **Response (`201 Created`):**
  ```json
  {
    "id": 2,
    "owner": 1,
    "title": "Pay rent",
    "description": "Remind me to pay rent on the 1st of every month",
    "completed": false,
    "due_date": "2026-12-01",
    "priority": "medium",
    "recurrence": "monthly",
    "parent": null,
    "created_at": "2026-11-21T15:02:00Z",
    "updated_at": "2026-11-21T15:02:00Z"
  }
  ```

---

### 3.3 Subtasks (`/api/tasks/{id}/subtasks/`)

#### List Subtasks
- **`GET /api/tasks/1/subtasks/`**
- **Response (`200 OK`):** Array of subtasks belonging to parent task 1.

#### Create Subtask
- **`POST /api/tasks/1/subtasks/`**
- **Request:**
  ```json
  {
    "title": "Collect finance statements",
    "description": "Export bank statements for review",
    "priority": "medium"
  }
  ```
- **Response (`201 Created`):** Subtask created with `parent: 1`.

---

### 3.4 Task Breakdown (`/api/tasks/{id}/breakdown/`)

- **`POST /api/tasks/1/breakdown/`**
- **Header (Optional):** `Idempotency-Key: <key>`
- If parent task title is compound (e.g., *"Clean bedroom, wash dishes and fold laundry"*), it automatically splits into individual subtasks.
- If parent title cannot be split, a 3-stage plan is generated:
  - `Plan: <title>`
  - `Do: <title>`
  - `Review: <title>`
- **Atomic execution**: Uses a database transaction. If any subtask fails validation, the entire transaction is rolled back and no partial subtasks are stored.
- **Response (`201 Created`):** Array of created subtasks.

---

## 4. Error Codes & Format

All error responses return the standard schema:
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable summary",
    "details": [
      {
        "field": "field_name",
        "issue": "Specific validation or business rule failure"
      }
    ]
  }
}
```

| HTTP Status | Error Code | Description / Trigger |
|-------------|------------|-----------------------|
| **400** | `VALIDATION_ERROR` | Schema validation failed (missing required field, empty/whitespace string, non-boolean type, invalid date format). |
| **400** | `MALFORMED_REQUEST` | Malformed JSON in request payload. |
| **400** | `SUBTASK_NOT_ALLOWED` | Attempting to create a subtask under another subtask (nested subtasks not allowed) or break down a subtask. |
| **404** | `NOT_FOUND` | Task ID does not exist, route does not match, or pagination page is out of range. |
| **422** | `IDEMPOTENCY_KEY_REUSED` | The same `Idempotency-Key` was submitted with a different request body. |
| **409** | `IDEMPOTENCY_REQUEST_IN_PROGRESS` | The same key and body are currently being processed. Retry the request shortly. |
| **502** | `PARSER_FAILED` | Internal text parser raised an unhandled exception. |
| **502** | `AI_OUTPUT_INVALID` | Parser or breakdown produced an invalid date (e.g. `2026-02-30`), empty title, or malformed fields. |
| **503** | `LLM_NOT_CONFIGURED` | LLM parser was configured (`TASK_PARSER=tasks.ai.parse_task_text_llm`), but `LLM_API_KEY` is not set. |

---

## 5. Parser Contract

Pluggable parsers must follow this Python function signature:
```python
def parse_task_text(text: str, today: datetime.date) -> dict:
    """
    Returns a dict with:
      - title: str
      - description: str (original input text)
      - due_date: Optional[str] (YYYY-MM-DD)
      - recurrence: Optional[str] (daily|weekly|monthly|yearly)
    """
```

### Re-validation Guarantee
Parser output is **never trusted implicitly**. Output from `parse_task_text` is passed through DRF's `TaskSerializer`. If the parser outputs an impossible date like `2026-02-30` or an empty title, the endpoint rejects the output with HTTP `502 AI_OUTPUT_INVALID` and commits nothing to the database.

---

## 6. Idempotency Architecture

### Implementation
- Decorated with `@idempotent` on state-mutating endpoints:
  - `POST /api/tasks/`
  - `POST /api/tasks/from-text/`
  - `POST /api/tasks/{id}/breakdown/`
- Requests with `Idempotency-Key` are stored in the shared database, scoped by user, method, path, and key. The unique key hash lets concurrent workers coordinate without a process-wide lock.
- Body JSON is normalized with sorted keys to detect genuine payload changes.
- Successful responses (`200 <= status < 300`) are stored and returned on replay with the header `Idempotent-Replayed: true`.
- If the same key is sent with a different body, it returns `422 IDEMPOTENCY_KEY_REUSED`.
- If an identical request arrives while its first request is still running, it returns `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`; clients can retry with the same key.
- Error responses are **never cached**; if a request fails validation, the client can correct the body and retry with the same key.
- Idempotency records persist in the database. Plan a retention and cleanup policy for long-running deployments.

---

## 7. Verification & Running Tests

Run the test suite with:
```bash
./venv/bin/python manage.py test
```
All unit and integration tests run across CRUD, validation, natural language parsing, error responses, breakdown atomicity, and idempotency replays.
