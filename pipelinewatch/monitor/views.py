import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Build, Project
from .notifications import notify_failure
from .webhooks import parse_payload, record_build, verify_request

PULSE_LENGTH = 30


def _success_rate(builds):
    """Share of passed builds among finished (passed or failed) ones, as a percentage."""
    finished = [b for b in builds if b.status in (Build.SUCCESS, Build.FAILED)]
    if not finished:
        return None
    return round(100 * sum(b.status == Build.SUCCESS for b in finished) / len(finished))


# --- Dashboard --------------------------------------------------------------
@login_required
def dashboard(request):
    projects = list(Project.objects.all())
    for project in projects:
        recent = list(project.builds.all()[:PULSE_LENGTH])  # newest first
        project.latest = recent[0] if recent else None
        project.pulse = list(reversed(recent))  # oldest -> newest, left to right
        project.rate = _success_rate(recent)

    counts = {
        "passing": sum(1 for p in projects if p.latest and p.latest.status == Build.SUCCESS),
        "failing": sum(1 for p in projects if p.latest and p.latest.status == Build.FAILED),
        "running": sum(1 for p in projects if p.latest and p.latest.status in Build.ACTIVE),
    }
    builds_today = Build.objects.filter(created_at__gte=timezone.now() - timedelta(hours=24)).count()

    return render(
        request,
        "monitor/dashboard.html",
        {"projects": projects, "counts": counts, "builds_today": builds_today},
    )


# --- Project detail ---------------------------------------------------------
def _daily_chart(project, days=14):
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (
        project.builds.filter(created_at__date__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            ok=Count("id", filter=Q(status=Build.SUCCESS)),
            bad=Count("id", filter=Q(status=Build.FAILED)),
        )
    )
    by_day = {row["day"]: row for row in rows}

    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = by_day.get(day, {"ok": 0, "bad": 0})
        series.append({"day": day, "ok": row["ok"], "bad": row["bad"]})

    peak = max([d["ok"] + d["bad"] for d in series] + [1])
    for d in series:
        d["ok_pct"] = int(d["ok"] * 100 / peak)
        d["bad_pct"] = int(d["bad"] * 100 / peak)
        d["label"] = d["day"].strftime("%d %b")
        d["short"] = d["day"].strftime("%d")
    return series


@login_required
def project_detail(request, slug):
    project = get_object_or_404(Project, slug=slug)

    builds = project.builds.all()
    branch = request.GET.get("branch", "")
    status = request.GET.get("status", "")
    if branch:
        builds = builds.filter(branch=branch)
    if status in dict(Build.STATUS_CHOICES):
        builds = builds.filter(status=status)
    else:
        status = ""

    page = Paginator(builds, 20).get_page(request.GET.get("page"))

    recent = list(project.builds.all()[:PULSE_LENGTH])
    passed = [b.duration_seconds for b in recent if b.status == Build.SUCCESS and b.duration_seconds]
    avg_seconds = int(sum(passed) / len(passed)) if passed else None

    params = request.GET.copy()
    params.pop("page", None)

    return render(
        request,
        "monitor/project_detail.html",
        {
            "project": project,
            "page": page,
            "branches": project.builds.exclude(branch="").values_list("branch", flat=True).distinct().order_by("branch"),
            "statuses": Build.STATUS_CHOICES,
            "selected_branch": branch,
            "selected_status": status,
            "querystring": params.urlencode(),
            "pulse": list(reversed(recent)),
            "rate": _success_rate(recent),
            "avg_duration": f"{avg_seconds // 60}m {avg_seconds % 60:02d}s" if avg_seconds else "n/a",
            "chart": _daily_chart(project),
            "webhook_url": request.build_absolute_uri(reverse("webhook", args=[project.slug])),
        },
    )


@login_required
def build_detail(request, pk):
    build = get_object_or_404(Build.objects.select_related("project"), pk=pk)
    return render(request, "monitor/build_detail.html", {"build": build})


# --- JSON used by the dashboard's live refresh -------------------------------
@login_required
def api_status(request):
    data = []
    for project in Project.objects.all():
        latest = project.builds.first()
        data.append(
            {
                "slug": project.slug,
                "build": latest.pk if latest else None,
                "status": latest.status if latest else None,
            }
        )
    return JsonResponse({"projects": data})


# --- Webhook receiver ---------------------------------------------------------
@csrf_exempt
@require_POST
def webhook(request, slug):
    project = get_object_or_404(Project, slug=slug)

    if not verify_request(request, project):
        return JsonResponse({"error": "Invalid or missing webhook secret."}, status=403)

    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "Body must be a JSON object."}, status=400)

    provider, data = parse_payload(request.headers, payload)
    if data is None:
        return JsonResponse({"ignored": True}, status=202)

    build, created, previous = record_build(project, provider, data)

    # Alert once, when a build first turns red.
    if build.status == Build.FAILED and previous != Build.FAILED:
        notify_failure(build, request.build_absolute_uri("/").rstrip("/"))

    return JsonResponse({"build": build.pk, "status": build.status}, status=201 if created else 200)
