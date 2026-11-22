import re
from calendar import monthrange
from datetime import date, timedelta
from django.conf import settings

class LLMNotConfigured(Exception):
    pass

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
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
            if d >= today:
                return d
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return None

def parse_task_text(text: str, today: date) -> dict:
    """text + today -> {"title","description","due_date"(YYYY-MM-DD str),"recurrence"?}"""
    s = text.strip()
    result = {"description": s}
    working = s
    recurrence = None
    for pat, val in RECURRENCE:
        if re.search(pat, working, flags=re.IGNORECASE):
            recurrence = val
            working = re.sub(pat, " ", working, flags=re.IGNORECASE)
            break

    due = None
    if (m := re.search(r"\b\d{4}-\d{2}-\d{2}\b", working)):
        due = m.group(0)  # left raw: serializer rejects invalid dates like 2026-02-30
        working = working.replace(due, " ")
    elif re.search(r"\bday\s+after\s+tomorrow\b", working, flags=re.IGNORECASE):
        due = (today + timedelta(days=2)).isoformat()
        working = re.sub(r"\bday\s+after\s+tomorrow\b", " ", working, flags=re.IGNORECASE)
    elif re.search(r"\btomorrow\b", working, flags=re.IGNORECASE):
        due = (today + timedelta(days=1)).isoformat()
        working = re.sub(r"\btomorrow\b", " ", working, flags=re.IGNORECASE)
    elif re.search(r"\btoday\b", working, flags=re.IGNORECASE):
        due = today.isoformat()
        working = re.sub(r"\btoday\b", " ", working, flags=re.IGNORECASE)
    elif (m := re.search(ORDINAL, working, flags=re.IGNORECASE)):
        d = _next_day_of_month(today, int(m.group(1)))
        due = d.isoformat() if d else None
        working = re.sub(ORDINAL, " ", working, count=1, flags=re.IGNORECASE)
    else:
        for i, name in enumerate(WEEKDAYS):
            if re.search(rf"\b(next\s+|on\s+)?{name}\b", working, flags=re.IGNORECASE):
                ahead = (i - today.weekday()) % 7 or 7
                due = (today + timedelta(days=ahead)).isoformat()
                working = re.sub(rf"\b(next\s+|on\s+)?{name}\b", " ", working, flags=re.IGNORECASE)
                break

    title = re.sub(LEAD_IN, "", re.sub(r"\s+", " ", working).strip(), flags=re.IGNORECASE).strip(" ,.-")
    result["title"] = title[:1].upper() + title[1:] if title else ""
    if due:
        result["due_date"] = due
    if recurrence:
        result["recurrence"] = recurrence
    return result

def break_down_task(task) -> list[dict]:
    parts = [p.strip(" .") for p in re.split(r",|\band\b", task.title) if p.strip(" .")]
    if len(parts) < 2:
        parts = [f"Plan: {task.title}", f"Do: {task.title}", f"Review: {task.title}"]
    return [
        {
            "title": p[:1].upper() + p[1:],
            "description": f"Part of: {task.title}",
            "priority": task.priority,
            **({"due_date": task.due_date.isoformat()} if task.due_date else {}),
        }
        for p in parts
    ]

def parse_task_text_llm(text: str, today: date) -> dict:
    if not getattr(settings, "LLM_API_KEY", None):
        raise LLMNotConfigured()
    raise NotImplementedError("LLM parser is not implemented yet")
