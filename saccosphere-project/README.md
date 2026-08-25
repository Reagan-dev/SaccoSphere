# SaccoSphere

## Railway Deployment Processes

Railway should run SaccoSphere as three separate services from the same
repository and environment. The Procfile defines all three process commands, but
for an existing Railway project you should still create or configure separate
Railway services for the API, Celery worker, and Celery beat scheduler. Do not
assume the current web service will automatically start worker and beat.

Required Railway services:

| Service | Start command | Notes |
| --- | --- | --- |
| Web/API | `python manage.py migrate && python manage.py collectstatic --noinput && gunicorn config.wsgi:application` | The only service that needs a public domain. |
| Worker | `celery -A config.celery worker -Q payments,notifications,reports,default -l info` | Consumes queued payment, notification, report, and default tasks. |
| Beat | `celery -A config.celery beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler` | Runs scheduled jobs through `django-celery-beat`. |

All three services must use `DJANGO_SETTINGS_MODULE=config.settings.production`
and share the same `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, and application
environment variables. `django-celery-beat` is installed in `requirements.txt`
and enabled through `INSTALLED_APPS`; the web service's migrate step applies its
database tables before beat starts using the database scheduler.

Celery uses Redis through `REDIS_URL`. In production settings,
`CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` both read from `REDIS_URL`, so
the Railway Redis private URL must be present on the web, worker, and beat
services.

## PDF Statement Generation On Render

SaccoSphere uses WeasyPrint to generate member statement PDFs. WeasyPrint needs
native system libraries in addition to the Python package.

On Render, install the required system packages during build before running
`pip install -r requirements.txt`. A typical Debian-based setup is:

```bash
apt-get update && apt-get install -y \
  libpango-1.0-0 \
  libpangoft2-1.0-0 \
  libcairo2 \
  libgdk-pixbuf-2.0-0 \
  libffi-dev \
  shared-mime-info
```

If these packages are missing, the JSON statement endpoint will still work, but
PDF generation may return `503 PDF generation temporarily unavailable.`
