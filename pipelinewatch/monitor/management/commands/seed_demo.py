import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import Build, Project

DEMOS = [
    ("Storefront API", "storefront-api", "https://github.com/acme/storefront-api", 0.88),
    ("Web App", "web-app", "https://github.com/acme/web-app", 0.72),
    ("Mobile Backend", "mobile-backend", "https://github.com/acme/mobile-backend", 0.55),
    ("Docs Site", "docs-site", "https://github.com/acme/docs-site", 0.97),
]
AUTHORS = ["Asha Raman", "Karthik S", "Meera Iyer", "Dev Patel"]
BRANCHES = ["main", "main", "main", "develop", "feature/checkout", "fix/login-timeout"]
MESSAGES = [
    "Fix cart total rounding",
    "Add retry to payment client",
    "Bump dependencies",
    "Refactor auth middleware",
    "Add pagination to orders endpoint",
    "Update README",
    "Speed up image resizing",
    "Handle empty search results",
]


def sample_log(status, seconds):
    lines = [
        "$ git checkout $COMMIT",
        "$ pip install -r requirements.txt",
        "Successfully installed 42 packages",
        "$ python manage.py test",
        "Found 87 test(s).",
    ]
    if status == Build.FAILED:
        lines += [
            "FAIL: test_checkout_total (orders.tests.CheckoutTests)",
            "AssertionError: Decimal('49.99') != Decimal('50.00')",
            "Ran 87 tests, FAILED (failures=1)",
        ]
    else:
        lines += ["Ran 87 tests", "OK"]
    lines.append(f"Finished in {seconds}s")
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Create demo projects, builds and a dev admin user (admin / admin12345)."

    def handle(self, *args, **options):
        rng = random.Random(7)
        now = timezone.now()

        for name, slug, repo, pass_rate in DEMOS:
            project, _ = Project.objects.get_or_create(
                slug=slug, defaults={"name": name, "repo_url": repo}
            )
            if project.builds.exists():
                continue

            for i in range(40):
                created = now - timedelta(hours=i * 8 + rng.randint(0, 5))
                status = Build.SUCCESS if rng.random() < pass_rate else Build.FAILED
                if slug == "mobile-backend" and i == 0:
                    status = Build.FAILED
                seconds = rng.randint(45, 420)
                Build.objects.create(
                    project=project,
                    provider="github",
                    external_id=f"demo-{slug}-{i}",
                    branch=rng.choice(BRANCHES),
                    commit_sha=f"{rng.getrandbits(40):010x}",
                    commit_message=rng.choice(MESSAGES),
                    author=rng.choice(AUTHORS),
                    status=status,
                    url=repo,
                    log=sample_log(status, seconds),
                    started_at=created,
                    finished_at=created + timedelta(seconds=seconds),
                    created_at=created,
                )

            if slug == "web-app":
                Build.objects.create(
                    project=project,
                    provider="github",
                    external_id=f"demo-{slug}-live",
                    branch="develop",
                    commit_sha=f"{rng.getrandbits(40):010x}",
                    commit_message="Add dark mode toggle",
                    author=AUTHORS[0],
                    status=Build.RUNNING,
                    started_at=now - timedelta(seconds=90),
                )

        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.com", "admin12345")
            self.stdout.write("Created dev user admin / admin12345")

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
