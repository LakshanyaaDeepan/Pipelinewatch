import json
import logging
import urllib.request

from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def notify_failure(build, base_url):
    """Send email and/or Slack alerts for a failed build.

    Runs inline with a short timeout. For high volume, move this into a task queue
    (Celery, Django-Q, RQ) so webhook responses stay fast.
    """
    project = build.project
    link = base_url + build.get_absolute_url()
    subject = f"[{project.name}] Build failed on {build.branch or 'unknown branch'}"
    body = (
        f"{subject}\n\n"
        f"Commit:  {build.short_sha} {build.commit_message}\n"
        f"Author:  {build.author or 'unknown'}\n"
        f"Details: {link}\n"
    )

    if project.notify_email:
        try:
            send_mail(subject, body, None, [project.notify_email])
        except Exception:
            logger.exception("Could not send failure email for build %s", build.pk)

    if project.slack_webhook_url:
        try:
            request = urllib.request.Request(
                project.slack_webhook_url,
                data=json.dumps({"text": f":red_circle: {subject}\n{build.commit_message}\n{link}"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=5).close()
        except Exception:
            logger.exception("Could not post Slack alert for build %s", build.pk)
