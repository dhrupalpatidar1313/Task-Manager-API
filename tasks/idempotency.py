import hashlib
import json
from datetime import timedelta
from functools import wraps

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.response import Response

from .errors import error_response


def clear_store():
    """Clear idempotency records, primarily for isolated test setup."""
    from .models import IdempotencyRecord

    IdempotencyRecord.objects.all().delete()


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def idempotent(view):
    @wraps(view)
    def wrapper(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if not key:
            return view(self, request, *args, **kwargs)

        from .models import IdempotencyRecord

        user_scope = getattr(request.user, "pk", "anonymous")
        scope = f"{user_scope}:{request.method}:{request.path}:{key}"
        key_hash = _digest(scope)
        body = json.dumps(request.data, sort_keys=True, separators=(",", ":"), default=str)
        request_hash = _digest(body)

        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(
                    key_hash=key_hash,
                    request_hash=request_hash,
                )
        except IntegrityError:
            record = IdempotencyRecord.objects.filter(key_hash=key_hash).first()
            if record is None:
                raise
            if record.request_hash != request_hash:
                return error_response(
                    "IDEMPOTENCY_KEY_REUSED",
                    "Idempotency-Key was already used with a different request body",
                    [{"field": "Idempotency-Key", "issue": "reused with a different body"}],
                    422,
                )
            if not record.is_complete:
                if record.created_at < timezone.now() - timedelta(minutes=10):
                    record.delete()
                    return wrapper(self, request, *args, **kwargs)
                return error_response(
                    "IDEMPOTENCY_REQUEST_IN_PROGRESS",
                    "A request with this Idempotency-Key is still in progress",
                    [{"field": "Idempotency-Key", "issue": "request is still in progress; retry shortly"}],
                    409,
                )
            replay = Response(record.response_data, status=record.response_status)
            replay["Idempotent-Replayed"] = "true"
            return replay

        try:
            response = view(self, request, *args, **kwargs)
            if 200 <= response.status_code < 300:
                response_json = json.loads(json.dumps(response.data, cls=DjangoJSONEncoder))
                record.response_data = response_json
                record.response_status = response.status_code
                record.save(update_fields=["response_data", "response_status"])
            else:
                record.delete()
            return response
        except Exception:
            record.delete()
            raise

    return wrapper
