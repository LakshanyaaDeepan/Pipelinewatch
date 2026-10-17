"""Webhook helpers: signature checks, payload parsing, and build upserts.

Supported senders:
  * GitHub Actions  -> `workflow_run` events (HMAC signature in X-Hub-Signature-256)
  * GitLab CI       -> `pipeline` events    (secret in X-Gitlab-Token)
  * Generic JSON    -> any tool / script    (secret in X-Webhook-Token)
"""
import hashlib
import hmac
from datetime import timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Build


# --- Authentication ---------------------------------------------------------
def verify_request(request, project):
    secret = project.webhook_secret.encode()

    signature = request.headers.get("X-Hub-Signature-256")
    if signature:
        expected = "sha256=" + hmac.new(secret, request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    token = request.headers.get("X-Gitlab-Token") or request.headers.get("X-Webhook-Token")
    if token:
        return hmac.compare_digest(token.encode(), secret)

    return False


# --- Parsing ----------------------------------------------------------------
def _dt(value):
    if not value or not isinstance(value, str):
        return None
    parsed = parse_datetime(value.replace(" UTC", "+00:00"))
    if parsed and timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def _first_line(text):
    lines = (text or "").strip().splitlines()
    return lines[0][:300] if lines else ""


GITHUB_CONCLUSIONS = {
    "success": Build.SUCCESS,
    "neutral": Build.SUCCESS,
    "failure": Build.FAILED,
    "timed_out": Build.FAILED,
    "startup_failure": Build.FAILED,
    "action_required": Build.FAILED,
    "cancelled": Build.CANCELLED,
    "skipped": Build.CANCELLED,
    "stale": Build.CANCELLED,
}


def parse_github(payload):
    run = payload.get("workflow_run")
    if not run or "id" not in run:
        return None

    gh_status = run.get("status")
    if gh_status == "completed":
        status = GITHUB_CONCLUSIONS.get(run.get("conclusion"), Build.FAILED)
    elif gh_status == "in_progress":
        status = Build.RUNNING
    else:
        status = Build.QUEUED

    commit = run.get("head_commit") or {}
    return {
        "external_id": str(run["id"]),
        "branch": run.get("head_branch") or "",
        "commit_sha": run.get("head_sha") or "",
        "commit_message": _first_line(commit.get("message") or run.get("display_title")),
        "author": (commit.get("author") or {}).get("name") or (run.get("actor") or {}).get("login", ""),
        "status": status,
        "url": run.get("html_url") or "",
        "started_at": _dt(run.get("run_started_at")),
        "finished_at": _dt(run.get("updated_at")) if gh_status == "completed" else None,
    }


GITLAB_STATUSES = {
    "created": Build.QUEUED,
    "pending": Build.QUEUED,
    "waiting_for_resource": Build.QUEUED,
    "preparing": Build.QUEUED,
    "scheduled": Build.QUEUED,
    "manual": Build.QUEUED,
    "running": Build.RUNNING,
    "success": Build.SUCCESS,
    "failed": Build.FAILED,
    "canceled": Build.CANCELLED,
    "skipped": Build.CANCELLED,
}


def parse_gitlab(payload):
    attrs = payload.get("object_attributes") or {}
    if "id" not in attrs:
        return None

    status = GITLAB_STATUSES.get(attrs.get("status"))
    if status is None:
        return None

    commit = payload.get("commit") or {}
    web_url = (payload.get("project") or {}).get("web_url", "")
    return {
        "external_id": str(attrs["id"]),
        "branch": attrs.get("ref") or "",
        "commit_sha": attrs.get("sha") or "",
        "commit_message": _first_line(commit.get("message")),
        "author": (commit.get("author") or {}).get("name") or (payload.get("user") or {}).get("name", ""),
        "status": status,
        "url": f"{web_url}/-/pipelines/{attrs['id']}" if web_url else "",
        "started_at": _dt(attrs.get("created_at")),
        "finished_at": _dt(attrs.get("finished_at")),
    }


GENERIC_STATUSES = {
    "queued": Build.QUEUED,
    "pending": Build.QUEUED,
    "running": Build.RUNNING,
    "in_progress": Build.RUNNING,
    "success": Build.SUCCESS,
    "passed": Build.SUCCESS,
    "failed": Build.FAILED,
    "failure": Build.FAILED,
    "error": Build.FAILED,
    "cancelled": Build.CANCELLED,
    "canceled": Build.CANCELLED,
}


def parse_generic(payload):
    """Minimal contract: {"id": "...", "status": "running|success|failed|cancelled", ...}"""
    if "id" not in payload or "status" not in payload:
        return None
    status = GENERIC_STATUSES.get(str(payload["status"]).lower())
    if status is None:
        return None

    now = timezone.now()
    started_at = _dt(payload.get("started_at"))
    finished_at = _dt(payload.get("finished_at"))
    if status == Build.RUNNING and not started_at:
        started_at = now
    if status in (Build.SUCCESS, Build.FAILED, Build.CANCELLED) and not finished_at:
        finished_at = now

    return {
        "external_id": str(payload["id"]),
        "branch": str(payload.get("branch", "")),
        "commit_sha": str(payload.get("commit", "")),
        "commit_message": _first_line(payload.get("message")),
        "author": str(payload.get("author", "")),
        "status": status,
        "url": str(payload.get("url", "")),
        "log": str(payload.get("log", "")),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def parse_payload(headers, payload):
    """Return (provider, parsed_dict_or_None). None means 'valid request, nothing to record'."""
    github_event = headers.get("X-GitHub-Event")
    if github_event:
        return "github", (parse_github(payload) if github_event == "workflow_run" else None)

    if headers.get("X-Gitlab-Event"):
        return "gitlab", (parse_gitlab(payload) if payload.get("object_kind") == "pipeline" else None)

    return "generic", parse_generic(payload)


# --- Persistence ------------------------------------------------------------
def record_build(project, provider, data):
    """Create or update a build. Returns (build, created, previous_status)."""
    data = dict(data)
    external_id = data.pop("external_id")

    build, created = Build.objects.get_or_create(
        project=project, provider=provider, external_id=external_id, defaults=data
    )
    if created:
        return build, True, None

    previous = build.status
    # Webhooks can arrive out of order; never move a finished build back to running.
    if previous not in Build.ACTIVE and data["status"] in Build.ACTIVE:
        return build, False, previous

    for field, value in data.items():
        if value in (None, ""):
            continue
        setattr(build, field, value)
    build.save()
    return build, False, previous
