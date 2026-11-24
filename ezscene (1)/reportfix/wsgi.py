import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "reportfix.settings")
application = get_wsgi_application()

# On Vercel, create/upgrade tables on cold start (idempotent).
if os.environ.get("VERCEL") or os.environ.get("AUTO_MIGRATE") == "1":
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)

app = application  # Vercel's Python runtime looks for `app`
