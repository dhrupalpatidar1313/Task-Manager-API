from datetime import date, timedelta
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework import status
from tasks.models import Task
from tasks.idempotency import clear_store

User = get_user_model()

class TaskAPITestCase(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="alice", password="Str0ng!Pass")
        self.client.force_authenticate(user=self.user)
        clear_store()

    # ==================== CRUD ====================

    def test_crud_lifecycle(self):
        # 1. Create
        payload = {
            "title": "Buy groceries",
            "description": "Milk, eggs, bread",
            "completed": False,
            "due_date": "2026-12-01",
            "priority": "high",
            "recurrence": "weekly",
        }
        res = self.client.post("/api/tasks/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        task_id = res.data["id"]
        self.assertEqual(res.data["title"], "Buy groceries")
        self.assertEqual(res.data["priority"], "high")

        # 2. List
        res = self.client.get("/api/tasks/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 1)

        # 3. Retrieve
        res = self.client.get(f"/api/tasks/{task_id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["id"], task_id)

        # 4. PUT (full update)
        put_payload = {
            "title": "Buy groceries and snacks",
            "description": "Milk, eggs, bread, chips",
            "completed": True,
            "due_date": "2026-12-02",
            "priority": "medium",
            "recurrence": "daily",
        }
        res = self.client.put(f"/api/tasks/{task_id}/", put_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["title"], "Buy groceries and snacks")
        self.assertTrue(res.data["completed"])

        # 5. PATCH (partial update)
        res = self.client.patch(f"/api/tasks/{task_id}/", {"priority": "low"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["priority"], "low")

        # 6. Delete
        res = self.client.delete(f"/api/tasks/{task_id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Task.objects.count(), 0)

    def test_retrieve_not_found(self):
        res = self.client.get("/api/tasks/99999/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["error"]["code"], "NOT_FOUND")
        self.assertEqual(res.data["error"]["message"], "Resource not found")
        self.assertIsInstance(res.data["error"]["code"], str)
        self.assertIsInstance(res.data["error"]["message"], str)
        self.assertEqual(res.data["error"]["details"], [])

    def test_retrieve_invalid_id_format_gives_404(self):
        res = self.client.get("/api/tasks/abc/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["error"]["code"], "NOT_FOUND")

    # ==================== VALIDATION ====================

    def test_validation_empty_and_whitespace_title(self):
        for bad_title in ["", "   "]:
            res = self.client.post("/api/tasks/", {"title": bad_title, "description": "Valid desc"}, format="json")
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(res.data["error"]["code"], "VALIDATION_ERROR")
            self.assertTrue(any(d["field"] == "title" for d in res.data["error"]["details"]))

    def test_validation_empty_and_whitespace_description(self):
        for bad_desc in ["", "   "]:
            res = self.client.post("/api/tasks/", {"title": "Valid title", "description": bad_desc}, format="json")
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(res.data["error"]["code"], "VALIDATION_ERROR")
            self.assertTrue(any(d["field"] == "description" for d in res.data["error"]["details"]))

    def test_validation_strict_boolean(self):
        for val in ["yes", "true", "1", 1, 0]:
            res = self.client.post("/api/tasks/", {
                "title": "Task", "description": "Desc", "completed": val
            }, format="json")
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(res.data["error"]["code"], "VALIDATION_ERROR")
            detail = next(d for d in res.data["error"]["details"] if d["field"] == "completed")
            self.assertEqual(detail["issue"], "must be a boolean (true or false)")

    def test_validation_strict_date(self):
        for bad_date in ["2026-02-30", "2026-2-3", "tomorrow", "31-12-2026"]:
            res = self.client.post("/api/tasks/", {
                "title": "Task", "description": "Desc", "due_date": bad_date
            }, format="json")
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(res.data["error"]["code"], "VALIDATION_ERROR")
            detail = next(d for d in res.data["error"]["details"] if d["field"] == "due_date")
            self.assertEqual(detail["issue"], "must be a real date in YYYY-MM-DD format")

    def test_validation_priority_choices(self):
        res = self.client.post("/api/tasks/", {
            "title": "Task", "description": "Desc", "priority": "urgent"
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"]["code"], "VALIDATION_ERROR")
        detail = next(d for d in res.data["error"]["details"] if d["field"] == "priority")
        self.assertEqual(detail["issue"], "must be one of low, medium, high")

    def test_validation_recurrence_choices(self):
        res = self.client.post("/api/tasks/", {
            "title": "Task", "description": "Desc", "recurrence": "biweekly"
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"]["code"], "VALIDATION_ERROR")
        detail = next(d for d in res.data["error"]["details"] if d["field"] == "recurrence")
        self.assertEqual(detail["issue"], "must be one of daily, weekly, monthly, yearly")

    def test_malformed_json_returns_malformed_request_error(self):
        res = self.client.post("/api/tasks/", "{bad json", content_type="application/json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"]["code"], "MALFORMED_REQUEST")
        self.assertIsInstance(res.data["error"]["message"], str)
        self.assertEqual(res.data["error"]["details"], [])

    # ==================== NATURAL LANGUAGE (FROM-TEXT) ====================

    def test_from_text_tomorrow(self):
        res = self.client.post("/api/tasks/from-text/", {
            "text": "Remind me to call John tomorrow"
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["title"], "Call John")
        expected_due = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(res.data["due_date"], expected_due)
        self.assertEqual(Task.objects.count(), 1)

    def test_from_text_1st_of_every_month(self):
        res = self.client.post("/api/tasks/from-text/", {
            "text": "Remind me to pay rent on the 1st of every month"
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["title"], "Pay rent")
        self.assertEqual(res.data["recurrence"], "monthly")
        self.assertTrue(res.data["due_date"].endswith("-01"))

    def test_from_text_empty_and_missing_text(self):
        res1 = self.client.post("/api/tasks/from-text/", {"text": ""}, format="json")
        self.assertEqual(res1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res1.data["error"]["code"], "VALIDATION_ERROR")

        res2 = self.client.post("/api/tasks/from-text/", {}, format="json")
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res2.data["error"]["code"], "VALIDATION_ERROR")

    def test_from_text_invalid_date_in_text_returns_502_and_no_row(self):
        count_before = Task.objects.count()
        res = self.client.post("/api/tasks/from-text/", {
            "text": "Submit taxes on 2026-02-30"
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(res.data["error"]["code"], "AI_OUTPUT_INVALID")
        self.assertEqual(Task.objects.count(), count_before)

    # ==================== BAD PARSER OUTPUT ====================

    @mock.patch("tasks.ai.parse_task_text")
    def test_bad_parser_output_empty_title_gives_502(self, mock_parser):
        mock_parser.return_value = {"title": "", "description": "Empty title returned"}
        count_before = Task.objects.count()
        res = self.client.post("/api/tasks/from-text/", {"text": "Do something"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(res.data["error"]["code"], "AI_OUTPUT_INVALID")
        self.assertEqual(Task.objects.count(), count_before)

    @mock.patch("tasks.ai.parse_task_text")
    def test_bad_parser_output_wrong_type_gives_502(self, mock_parser):
        mock_parser.return_value = {"title": "Task", "description": "Desc", "completed": "invalid_bool"}
        count_before = Task.objects.count()
        res = self.client.post("/api/tasks/from-text/", {"text": "Do something"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(res.data["error"]["code"], "AI_OUTPUT_INVALID")
        self.assertEqual(Task.objects.count(), count_before)

    @mock.patch("tasks.ai.parse_task_text")
    def test_parser_raised_exception_gives_502(self, mock_parser):
        mock_parser.side_effect = RuntimeError("Parser crashed")
        count_before = Task.objects.count()
        res = self.client.post("/api/tasks/from-text/", {"text": "Do something"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(res.data["error"]["code"], "PARSER_FAILED")
        self.assertEqual(Task.objects.count(), count_before)

    # ==================== LLM KEY CONFIGURATION ====================

    @override_settings(TASK_PARSER="tasks.ai.parse_task_text_llm", LLM_API_KEY=None)
    def test_llm_key_missing_returns_503(self):
        res = self.client.post("/api/tasks/from-text/", {"text": "Do something"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(res.data["error"]["code"], "LLM_NOT_CONFIGURED")

    # ==================== SUBTASKS ====================

    def test_subtasks_workflow(self):
        parent = Task.objects.create(owner=self.user, title="Parent Task", description="Parent Description")

        # 1. Create subtask -> 201
        res = self.client.post(f"/api/tasks/{parent.id}/subtasks/", {
            "title": "Subtask 1",
            "description": "First subtask",
            "priority": "low"
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["parent"], parent.id)
        subtask_id = res.data["id"]

        # 2. List subtasks
        res = self.client.get(f"/api/tasks/{parent.id}/subtasks/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["id"], subtask_id)

        # 3. Subtask of a subtask -> 400 SUBTASK_NOT_ALLOWED
        res = self.client.post(f"/api/tasks/{subtask_id}/subtasks/", {
            "title": "Nested Subtask",
            "description": "Not allowed"
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"]["code"], "SUBTASK_NOT_ALLOWED")

        # 4. Breakdown of a subtask -> 400 SUBTASK_NOT_ALLOWED
        res = self.client.post(f"/api/tasks/{subtask_id}/breakdown/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"]["code"], "SUBTASK_NOT_ALLOWED")

        # 5. Cascade delete
        res = self.client.delete(f"/api/tasks/{parent.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Task.objects.count(), 0)

    # ==================== BREAKDOWN ====================

    def test_breakdown_split_on_and_and_commas(self):
        task = Task.objects.create(
            owner=self.user,
            title="Clean bedroom, wash dishes and fold laundry",
            description="Chores list",
            priority="high",
            due_date=date(2026, 12, 1)
        )
        res = self.client.post(f"/api/tasks/{task.id}/breakdown/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data), 3)
        titles = [t["title"] for t in res.data]
        self.assertEqual(titles, ["Clean bedroom", "Wash dishes", "Fold laundry"])
        for item in res.data:
            self.assertEqual(item["priority"], "high")
            self.assertEqual(item["due_date"], "2026-12-01")
            self.assertEqual(item["parent"], task.id)

    def test_breakdown_template_fallback(self):
        task = Task.objects.create(
            owner=self.user,
            title="Write report",
            description="Annual report"
        )
        res = self.client.post(f"/api/tasks/{task.id}/breakdown/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data), 3)
        titles = [t["title"] for t in res.data]
        self.assertEqual(titles, ["Plan: Write report", "Do: Write report", "Review: Write report"])

    @mock.patch("tasks.views.break_down_task")
    def test_breakdown_invalid_item_rolls_back_all(self, mock_breakdown):
        task = Task.objects.create(owner=self.user, title="Master Task", description="Master")
        # One valid, one invalid item
        mock_breakdown.return_value = [
            {"title": "Valid Part", "description": "Desc"},
            {"title": "", "description": "Invalid empty title"},
        ]
        res = self.client.post(f"/api/tasks/{task.id}/breakdown/")
        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(res.data["error"]["code"], "AI_OUTPUT_INVALID")
        # Ensure zero subtasks created due to atomic transaction
        self.assertEqual(task.subtasks.count(), 0)

    # ==================== IDEMPOTENCY ====================

    def test_idempotency_same_key_same_body(self):
        payload = {"title": "Task 1", "description": "Desc"}
        headers = {"HTTP_IDEMPOTENCY_KEY": "test-key-1"}

        res1 = self.client.post("/api/tasks/", payload, format="json", **headers)
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        task_id = res1.data["id"]

        res2 = self.client.post("/api/tasks/", payload, format="json", **headers)
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.data["id"], task_id)
        self.assertEqual(res2.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(Task.objects.count(), 1)

    def test_idempotency_same_key_different_body_gives_422(self):
        headers = {"HTTP_IDEMPOTENCY_KEY": "test-key-2"}
        res1 = self.client.post("/api/tasks/", {"title": "Original", "description": "Desc"}, format="json", **headers)
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        res2 = self.client.post("/api/tasks/", {"title": "Different", "description": "Desc"}, format="json", **headers)
        self.assertEqual(res2.status_code, 422)
        self.assertEqual(res2.data["error"]["code"], "IDEMPOTENCY_KEY_REUSED")

    def test_idempotency_failed_request_retried_with_fixed_body_succeeds(self):
        headers = {"HTTP_IDEMPOTENCY_KEY": "retry-key"}
        # 1. Invalid request fails with 400
        res1 = self.client.post("/api/tasks/", {"title": "", "description": "Desc"}, format="json", **headers)
        self.assertEqual(res1.status_code, status.HTTP_400_BAD_REQUEST)

        # 2. Fixed request with same key succeeds with 201
        res2 = self.client.post("/api/tasks/", {"title": "Fixed Title", "description": "Desc"}, format="json", **headers)
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Task.objects.count(), 1)

    @mock.patch("tasks.ai.parse_task_text")
    def test_idempotency_parser_called_only_once_on_replay(self, mock_parser):
        mock_parser.return_value = {"title": "Parsed Task", "description": "Original text"}
        headers = {"HTTP_IDEMPOTENCY_KEY": "from-text-key"}

        res1 = self.client.post("/api/tasks/from-text/", {"text": "Do something"}, format="json", **headers)
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        res2 = self.client.post("/api/tasks/from-text/", {"text": "Do something"}, format="json", **headers)
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(mock_parser.call_count, 1)

    def test_idempotency_no_header_creates_multiple_rows(self):
        payload = {"title": "Non-idempotent", "description": "Desc"}
        res1 = self.client.post("/api/tasks/", payload, format="json")
        res2 = self.client.post("/api/tasks/", payload, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Task.objects.count(), 2)

    # ==================== QUERY / FILTER / ORDERING / PAGINATION ====================

    def test_combined_query_filter_search_ordering_pagination(self):
        Task.objects.create(
            owner=self.user,
            title="Pay rent for house",
            description="Transfer monthly rent money",
            completed=False,
            priority="high",
            due_date=date(2026, 6, 1)
        )
        Task.objects.create(
            owner=self.user,
            title="Rent a movie",
            description="Movie night",
            completed=False,
            priority="high",
            due_date=date(2026, 6, 15)
        )
        Task.objects.create(
            owner=self.user,
            title="Old rent payment",
            description="Already done",
            completed=True,
            priority="high",
            due_date=date(2026, 5, 1)
        )
        Task.objects.create(
            owner=self.user,
            title="Buy groceries",
            description="No rent here",
            completed=False,
            priority="low",
            due_date=date(2026, 7, 1)
        )

        url = "/api/tasks/?completed=false&priority=high&due_before=2026-12-31&search=rent&ordering=due_date&page=1&page_size=5"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 2)
        results = res.data["results"]
        self.assertEqual(results[0]["title"], "Pay rent for house")
        self.assertEqual(results[1]["title"], "Rent a movie")

    def test_pagination_out_of_range_returns_404_in_standard_shape(self):
        Task.objects.create(owner=self.user, title="Task 1", description="Desc")
        res = self.client.get("/api/tasks/?page=999")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["error"]["code"], "NOT_FOUND")
        self.assertIsInstance(res.data["error"]["message"], str)
        self.assertEqual(res.data["error"]["details"], [])

    def test_invalid_filter_value_returns_400_in_standard_shape(self):
        res = self.client.get("/api/tasks/?completed=maybe")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"]["code"], "VALIDATION_ERROR")
        self.assertIsInstance(res.data["error"]["message"], str)
        self.assertTrue(len(res.data["error"]["details"]) > 0)

    # ==================== AUTH / OWNERSHIP ====================

    def test_unauthenticated_request_is_rejected(self):
        self.client.force_authenticate(user=None)
        res = self.client.get("/api/tasks/")
        self.assertIn(res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_tasks_are_scoped_to_owner(self):
        other = User.objects.create_user(username="bob", password="Str0ng!Pass")
        Task.objects.create(owner=other, title="Bobs secret task", description="Hidden")
        Task.objects.create(owner=self.user, title="Alices task", description="Visible")

        res = self.client.get("/api/tasks/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 1)
        self.assertEqual(res.data["results"][0]["title"], "Alices task")

        hidden = Task.objects.get(title="Bobs secret task")
        res = self.client.get(f"/api/tasks/{hidden.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        res = self.client.delete(f"/api/tasks/{hidden.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Task.objects.filter(id=hidden.id).exists())

    def test_created_task_records_owner(self):
        res = self.client.post("/api/tasks/", {"title": "Mine", "description": "Desc"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["owner"], self.user.id)
        self.assertEqual(Task.objects.get(id=res.data["id"]).owner, self.user)
