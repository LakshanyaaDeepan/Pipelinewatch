import secrets

from django.db import models
from django.urls import reverse
from django.utils import timezone


def generate_secret():
    return secrets.token_urlsafe(24)


class Project(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, help_text="Used in the webhook URL.")
    repo_url = models.URLField(blank=True)
    webhook_secret = models.CharField(max_length=64, default=generate_secret)
    notify_email = models.EmailField(blank=True, help_text="Alert address for failed builds.")
    slack_webhook_url = models.URLField(blank=True, help_text="Slack incoming webhook for failed builds.")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("project_detail", args=[self.slug])


class Build(models.Model):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (SUCCESS, "Passed"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]
    ACTIVE = (QUEUED, RUNNING)

    PROVIDER_CHOICES = [("github", "GitHub"), ("gitlab", "GitLab"), ("generic", "Generic")]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="builds")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="generic")
    external_id = models.CharField(max_length=100, help_text="Run/pipeline id from the CI system.")
    branch = models.CharField(max_length=200, blank=True)
    commit_sha = models.CharField(max_length=64, blank=True)
    commit_message = models.CharField(max_length=300, blank=True)
    author = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    url = models.URLField(max_length=500, blank=True)
    log = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "provider", "external_id"], name="unique_build_per_run"
            )
        ]

    def __str__(self):
        return f"{self.project.name} #{self.external_id} ({self.status})"

    def get_absolute_url(self):
        return reverse("build_detail", args=[self.pk])

    @property
    def short_sha(self):
        return self.commit_sha[:7]

    @property
    def duration_seconds(self):
        if not self.started_at:
            return None
        end = self.finished_at or (timezone.now() if self.status in self.ACTIVE else None)
        if end is None:
            return None
        return max(int((end - self.started_at).total_seconds()), 0)

    @property
    def duration_display(self):
        seconds = self.duration_seconds
        if seconds is None:
            return "n/a"
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
