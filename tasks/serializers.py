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
        return super().to_internal_value(value)

class TaskSerializer(serializers.ModelSerializer):
    completed = StrictBooleanField(required=False, default=False)
    due_date = StrictDateField(required=False, allow_null=True, input_formats=["%Y-%m-%d"])
    priority = serializers.ChoiceField(
        choices=Task.Priority.choices,
        required=False,
        default="medium",
        error_messages={"invalid_choice": "must be one of low, medium, high"},
    )
    recurrence = serializers.ChoiceField(
        choices=Task.Recurrence.choices,
        required=False,
        allow_blank=True,
        error_messages={"invalid_choice": "must be one of daily, weekly, monthly, yearly"},
    )
    parent = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "owner",
            "title",
            "description",
            "completed",
            "due_date",
            "priority",
            "recurrence",
            "parent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner", "created_at", "updated_at"]

class FromTextSerializer(serializers.Serializer):
    text = serializers.CharField()
