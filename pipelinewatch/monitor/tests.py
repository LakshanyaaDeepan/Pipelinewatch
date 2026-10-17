import hashlib
import hmac
import json

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from .models import Build, Project


class WebhookTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Demo", slug="demo", webhook_secret="s3cret", notify_email="team@example.com"
        )
        self.url = reverse("webhook", args=["demo"])

    def post_generic(self, token="s3cret", **overrides):
        payload = {
            "id": "1",
            "status": "running",
            "branch": "main",
            "commit": "abc1234def",
            "message": "Add feature",
            "author": "Asha",
        }
        payload.update(overrides)
        extra = {"HTTP_X_WEBHOOK_TOKEN": token} if token else {}
        return self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **extra
        )

    def test_rejects_missing_or_wrong_secret(self):
        self.assertEqual(self.post_generic(token=None).status_code, 403)
        self.assertEqual(self.post_generic(token="nope").status_code, 403)
        self.assertEqual(Build.objects.count(), 0)

    def test_creates_then_updates_same_build(self):
        first = self.post_generic()
        self.assertEqual(first.status_code, 201)
        second = self.post_generic(status="success")
        self.assertEqual(second.status_code, 200)

        self.assertEqual(Build.objects.count(), 1)
        build = Build.objects.get()
        self.assertEqual(build.status, Build.SUCCESS)
        self.assertEqual(build.short_sha, "abc1234")
        self.assertIsNotNone(build.finished_at)

    def test_failure_alert_sent_once(self):
        self.post_generic()
        self.post_generic(status="failed")
        self.post_generic(status="failed")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Build failed", mail.outbox[0].subject)

    def test_finished_build_is_not_reopened_by_late_event(self):
        self.post_generic(status="success")
        self.post_generic(status="running")
        self.assertEqual(Build.objects.get().status, Build.SUCCESS)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            self.url, data="not json", content_type="application/json", HTTP_X_WEBHOOK_TOKEN="s3cret"
        )
        self.assertEqual(response.status_code, 400)

    def test_github_workflow_run_with_signature(self):
        payload = {
            "workflow_run": {
                "id": 987,
                "status": "completed",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "deadbeefcafe",
                "html_url": "https://github.com/acme/demo/actions/runs/987",
                "run_started_at": "2026-10-16T08:00:00Z",
                "updated_at": "2026-10-16T08:03:10Z",
                "head_commit": {"message": "Fix bug\n\nlong body", "author": {"name": "Karthik"}},
            }
        }
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
            HTTP_X_GITHUB_EVENT="workflow_run",
        )
        self.assertEqual(response.status_code, 201)
        build = Build.objects.get()
        self.assertEqual(build.provider, "github")
        self.assertEqual(build.status, Build.FAILED)
        self.assertEqual(build.commit_message, "Fix bug")
        self.assertEqual(build.duration_seconds, 190)

    def test_github_bad_signature_rejected(self):
        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=wrong",
            HTTP_X_GITHUB_EVENT="workflow_run",
        )
        self.assertEqual(response.status_code, 403)

    def test_github_ping_is_ignored(self):
        body = b"{}"
        signature = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
            HTTP_X_GITHUB_EVENT="ping",
        )
        self.assertEqual(response.status_code, 202)

    def test_gitlab_pipeline(self):
        payload = {
            "object_kind": "pipeline",
            "object_attributes": {
                "id": 31,
                "ref": "develop",
                "sha": "bcbb5ec396a2c0f828686f14fac9b80b780504f2",
                "status": "success",
                "created_at": "2026-10-16 08:00:00 UTC",
                "finished_at": "2026-10-16 08:02:00 UTC",
            },
            "commit": {"message": "Update deps", "author": {"name": "Meera"}},
            "project": {"web_url": "https://gitlab.com/acme/demo"},
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_GITLAB_TOKEN="s3cret",
            HTTP_X_GITLAB_EVENT="Pipeline Hook",
        )
        self.assertEqual(response.status_code, 201)
        build = Build.objects.get()
        self.assertEqual(build.provider, "gitlab")
        self.assertEqual(build.status, Build.SUCCESS)
        self.assertEqual(build.duration_seconds, 120)


class PageTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Demo", slug="demo")
        self.build = Build.objects.create(
            project=self.project, external_id="1", status=Build.FAILED, log="boom"
        )
        self.user = get_user_model().objects.create_user("dev", password="pw12345!")

    def test_pages_require_login(self):
        for url in ["/", self.project.get_absolute_url(), self.build.get_absolute_url()]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/accounts/login/", response["Location"])

    def test_pages_render_for_signed_in_user(self):
        self.client.force_login(self.user)
        for url in ["/", self.project.get_absolute_url(), self.build.get_absolute_url(), reverse("api_status")]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_project_filters(self):
        Build.objects.create(project=self.project, external_id="2", status=Build.SUCCESS, branch="main")
        self.client.force_login(self.user)
        response = self.client.get(self.project.get_absolute_url(), {"status": "success"})
        self.assertEqual(len(response.context["page"].object_list), 1)
