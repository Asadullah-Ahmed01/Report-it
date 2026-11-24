import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from .env (no extra dependency). Real env vars win."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-reportfix-codequest-2026-dev-key")
ON_VERCEL = bool(os.environ.get("VERCEL"))
DEBUG = os.environ.get("DJANGO_DEBUG", "0" if ON_VERCEL else "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.sessions",
    "reportfix",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# --- Authentication / session precautions ---------------------------------
# Server-side sessions (DB table django_session): the browser only holds a random
# session id, so a user cannot forge "I am admin" by editing a cookie.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True          # page scripts cannot read the session cookie
SESSION_COOKIE_SAMESITE = "Lax"         # not sent on cross-site form posts
SESSION_COOKIE_SECURE = not DEBUG       # HTTPS-only cookie when deployed
SESSION_COOKIE_AGE = 8 * 60 * 60        # sign in again after 8 hours
CSRF_COOKIE_SECURE = not DEBUG
CSRF_TRUSTED_ORIGINS = ["https://*.vercel.app", "http://localhost:8000", "http://127.0.0.1:8000"] + [
    o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o
]
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
# Passwords are stored as salted PBKDF2-SHA256 hashes (Django default hasher).

# Judge mode: the hackathon rules require "switch to any user in one click, with no
# passwords". JUDGE_MODE=1 keeps that switcher; set JUDGE_MODE=0 to require login.
JUDGE_MODE = os.environ.get("JUDGE_MODE", "1") == "1"
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "CodeQuest26!")      # seeded students + staff
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Registrar@26")    # seeded admin
LOGIN_MAX_FAILURES = 5          # per account, then locked
LOGIN_LOCK_MINUTES = 5
LOGIN_IP_MAX_FAILURES = 20      # per IP address per 10 minutes

ROOT_URLCONF = "reportfix.urls"
WSGI_APPLICATION = "reportfix.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

# Database: set DATABASE_URL (postgres://..., mysql://...) to use a shared database.
# Without it: db.sqlite3 next to manage.py (laptop), or /tmp on Vercel (temporary).
_sqlite_path = Path("/tmp/reportfix.sqlite3") if ON_VERCEL else BASE_DIR / "db.sqlite3"
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{_sqlite_path}",
        conn_max_age=0,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Asia/Karachi"

# AI (Google Gemini). Without a key, the keyword fallback classifier is used.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT", "12"))
