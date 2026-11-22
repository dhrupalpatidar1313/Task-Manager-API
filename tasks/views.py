from datetime import date
from django.conf import settings
from django.db import transaction
from django.utils.module_loading import import_string
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Task
from .serializers import TaskSerializer, FromTextSerializer
from .errors import (
    error_response,
    validation_error_response,
    errors_to_details,
)
from .filters import TaskFilter
from .idempotency import idempotent
from .ai import break_down_task, LLMNotConfigured

class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    filterset_class = TaskFilter
    search_fields = ["title", "description"]
    ordering_fields = ["due_date", "priority", "created_at", "title"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

    @idempotent
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

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

        s = TaskSerializer(data=data)
        if not s.is_valid():
            return validation_error_response(
                s,
                status=502,
                code="AI_OUTPUT_INVALID",
                message="Parser returned invalid data",
            )
        s.save(owner=request.user)
        return Response(s.data, status=status.HTTP_201_CREATED)

    # ---- Subtasks ----
    @action(detail=True, methods=["get", "post"], url_path="subtasks")
    def subtasks(self, request, pk=None):
        task = self.get_object()
        if request.method == "GET":
            return Response(TaskSerializer(task.subtasks.all(), many=True).data)
        if task.parent_id is not None:
            return error_response("SUBTASK_NOT_ALLOWED", "A subtask cannot have subtasks", [], 400)
        s = TaskSerializer(data=request.data)
        if not s.is_valid():
            return validation_error_response(s)
        s.save(owner=request.user, parent=task)
        return Response(s.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="breakdown")
    @idempotent
    def breakdown(self, request, pk=None):
        task = self.get_object()
        if task.parent_id is not None:
            return error_response("SUBTASK_NOT_ALLOWED", "A subtask cannot be broken down", [], 400)
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
            return error_response("AI_OUTPUT_INVALID", "Breakdown produced invalid items", details, 502)
        with transaction.atomic():
            for s in serializers_:
                s.save(owner=request.user, parent=task)
        return Response([s.data for s in serializers_], status=status.HTTP_201_CREATED)
