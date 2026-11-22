from django.http import Http404, JsonResponse
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler

ISSUE_MAP = {
    "This field may not be blank.": "must not be empty",
    "This field is required.": "is required",
    "This field may not be null.": "must not be null",
}

def error_response(code, message, details=None, status=400):
    return Response(
        {
            "error": {
                "code": str(code),
                "message": str(message),
                "details": details or [],
            }
        },
        status=status,
    )

def errors_to_details(errors, prefix=""):
    """Flatten serializer.errors into [{'field', 'issue'}], all strings."""
    out = []
    if isinstance(errors, dict):
        for k, v in errors.items():
            field_name = f"{prefix}.{k}" if prefix else str(k)
            out += errors_to_details(v, field_name)
    elif isinstance(errors, list):
        for i, item in enumerate(errors):
            if isinstance(item, (dict, list)):
                out += errors_to_details(item, f"{prefix}[{i}]")
            else:
                s = str(item)
                out.append({
                    "field": prefix or "non_field_errors",
                    "issue": ISSUE_MAP.get(s, s),
                })
    else:
        s = str(errors)
        out.append({
            "field": prefix or "non_field_errors",
            "issue": ISSUE_MAP.get(s, s),
        })
    return out

def validation_error_response(serializer, status=400, code="VALIDATION_ERROR",
                              message="Validation failed", prefix=""):
    return error_response(code, message, errors_to_details(serializer.errors, prefix), status)

def not_found(what="Task"):
    return error_response("NOT_FOUND", f"{what} not found", [], 404)

def custom_exception_handler(exc, context):
    """Safety net: bad JSON, invalid filter values, bad page numbers -> same shape."""
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, exceptions.ValidationError):
        return error_response(
            "VALIDATION_ERROR",
            "Validation failed",
            errors_to_details(exc.detail),
            400,
        )
    if isinstance(exc, (exceptions.NotFound, Http404)):
        return error_response("NOT_FOUND", "Resource not found", [], 404)
    if isinstance(exc, exceptions.ParseError):
        return error_response("MALFORMED_REQUEST", "Malformed request body", [], 400)
    return error_response(
        getattr(exc, "default_code", "ERROR").upper(),
        str(exc.detail) if hasattr(exc, "detail") else "Error",
        [],
        response.status_code,
    )

def json_404_handler(request, exception=None):
    return JsonResponse(
        {
            "error": {
                "code": "NOT_FOUND",
                "message": "Resource not found",
                "details": [],
            }
        },
        status=404,
    )
