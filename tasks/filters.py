from django import forms
from django.core.exceptions import ValidationError
import django_filters as df
from .models import Task

class StrictNullBooleanField(forms.Field):
    def to_python(self, value):
        if value in (None, "", "null"):
            return None
        if value in (True, "True", "true", "1"):
            return True
        if value in (False, "False", "false", "0"):
            return False
        raise ValidationError("Enter a valid boolean value (true or false).", code="invalid")

class StrictBooleanFilter(df.BooleanFilter):
    field_class = StrictNullBooleanField

class TaskFilter(df.FilterSet):
    completed = StrictBooleanFilter()
    due_before = df.DateFilter(field_name="due_date", lookup_expr="lte")
    due_after = df.DateFilter(field_name="due_date", lookup_expr="gte")
    is_subtask = StrictBooleanFilter(field_name="parent", lookup_expr="isnull", exclude=True)

    class Meta:
        model = Task
        fields = {
            "priority": ["exact"],
            "parent": ["exact"],
        }
