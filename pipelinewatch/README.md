# Pipelinewatch: a CI/CD status monitor in Django

Receives build events from GitHub Actions, GitLab CI, or any script, then shows
live pipeline health: a pulse strip of recent builds, pass rate, average duration,
a 14-day chart, filterable history, build logs, and failure alerts by email or Slack.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py makemigrations monitor
python manage.py migrate
python manage.py seed_demo       # demo projects + dev login: admin / admin12345
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in. Run the tests with `python manage.py test`.

## Add a real project

1. Go to **Manage projects** (the Django admin) and add a project. Copy its webhook secret.
2. Point your CI at `http://<host>/webhooks/<slug>/`.
   Localhost is not reachable from GitHub; use a tunnel such as `cloudflared` or `ngrok`,
   and set `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` for its hostname.

| Sender | Setup |
| --- | --- |
| GitHub | Repo settings, Webhooks: content type `application/json`, your secret, event **Workflow runs** |
| GitLab | Settings, Webhooks: secret token, trigger **Pipeline events** |
| Anything else | `POST` JSON with an `X-Webhook-Token: <secret>` header |

Generic payload (only `id` and `status` are required):

```bash
curl -X POST http://127.0.0.1:8000/webhooks/storefront-api/ \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: <secret>" \
  -d '{"id":"101","status":"running","branch":"main","commit":"a1b2c3d4",
       "message":"Fix cart total","author":"Asha"}'
```

Send a second request with the same `id` and `"status":"failed"` (optionally with a
`"log"` field) to update the build. A failure alert goes out the first time a build turns red.

## Alerts

Set an email address and/or Slack incoming-webhook URL on the project in the admin.
In development, emails print to the terminal. For real email, set
`DJANGO_EMAIL_BACKEND` and the usual `EMAIL_*` settings.

## Layout

```
config/                 project settings and root urls
monitor/models.py       Project, Build
monitor/webhooks.py     signature checks, GitHub/GitLab/generic parsers, upsert logic
monitor/notifications.py email + Slack alerts
monitor/views.py        dashboard, project page, build page, webhook, status JSON
monitor/templates/      base, dashboard, project, build, login
monitor/management/     seed_demo command
```

## Production notes

* Set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`; run behind gunicorn and HTTPS.
* Run `python manage.py collectstatic` and serve `staticfiles/` (WhiteNoise is the easiest option).
* Move alerts into a task queue (Celery or Django-Q) if you expect many failures at once.
* Switch to PostgreSQL by changing `DATABASES`.

## Ideas to extend

Per-user project access, rotating secrets from the UI, a retry button that calls the CI API,
build duration trend charts, flaky-test detection, and WebSockets (Django Channels) instead of polling.
