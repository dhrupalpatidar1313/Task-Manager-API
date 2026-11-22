from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tasks", "0002_task_owner")]

    operations = [
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key_hash", models.CharField(max_length=64, unique=True)),
                ("request_hash", models.CharField(max_length=64)),
                ("response_data", models.JSONField(blank=True, null=True)),
                ("response_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
