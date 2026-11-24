# EzScene — campus issue reporting (CodeQuest 26, Django)

Students report campus problems in English, Roman Urdu or both; Gemini reads the text, plain Django code
decides department, priority, privacy, votes and trust. One Django app serves the JSON API and the web UI.
Full design, rules and API contract: `docs/PROJECT.md`.

## Run on a laptop (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then put your GEMINI_API_KEY (and DATABASE_URL if shared) in .env
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000 (desktop browser). macOS/Linux: `source .venv/bin/activate` and `cp .env.example .env`.

Pages: `/login/` · student `/student/`, `/board/` · staff `/staff/<department>/` · admin `/admin/`, `/admin/staff/`,
`/admin/students/`, `/admin/reports/` · issue `/issues/<code>/` · ratings `/people/<id>/`. Demo logins: `docs/DEMO_ACCOUNTS.md`.
Method reference: `docs/METHODS.md`. Tables: `docs/DATABASE.md`. Tests: `python tests/<name>.py .`

- First request seeds demo users and issues automatically.
- Judge bar (top): **Act as** switches user in one click (no passwords), **+ New student**, **Reset data**.
- The pill at the top right shows whether Gemini or the keyword fallback is answering.
- Check AI from the browser: http://127.0.0.1:8000/api/health?test=1
- CLI version still works: `python manage.py reportfix_cli`.

## Environment variables (`.env`)

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Gemini key from aistudio.google.com. Without it, the keyword fallback runs everything. |
| `GEMINI_MODEL` | Default `gemini-2.5-flash`. |
| `DATABASE_URL` | Shared database, e.g. `postgres://user:pass@host:5432/db?sslmode=require`. Empty = local `db.sqlite3`. |

After setting `DATABASE_URL`, run `python manage.py migrate` once to create the tables.

## Deploy on Vercel

Vercel detects Django from `manage.py` (zero config). Import the repo, add `GEMINI_API_KEY` and
`DATABASE_URL` in Settings → Environment Variables, deploy. Tables are migrated on cold start.
Without `DATABASE_URL`, Vercel uses a temporary SQLite file that resets, so set the shared database.

## Layout

| File | What it does |
| --- | --- |
| `reportfix/models.py` | Users, trust log, issues, reports, votes, events, flags; priority formula |
| `reportfix/places.py` | Campus map; each place belongs to one department |
| `reportfix/classify.py` | Code-side detectors (hazard, harassment, injection, tone) + keyword fallback |
| `reportfix/ai.py` | Gemini triage (facts only, JSON schema) and admin summary |
| `reportfix/engine.py` | Rules: routing, grouping, priority, votes, trust, brigading |
| `reportfix/api.py` | JSON API with server-side permission checks |
| `reportfix/templates/reportfix/index.html` | Web UI (vanilla JS) |
| `reportfix/management/commands/reportfix_cli.py` | Interactive CLI |
