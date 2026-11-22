import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def delete_ownerless_tasks(apps, schema_editor):
    Task = apps.get_model("tasks", "Task")
    Task.objects.filter(owner__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tasks", "0003_idempotencyrecord"),
    ]

    operations = [
        migrations.RunPython(delete_ownerless_tasks, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="task",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tasks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
