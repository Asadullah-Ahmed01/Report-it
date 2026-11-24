# ReportFix database (Django models, `reportfix/models.py`)

Tested: 238 checks — 88 requirement checks on a fresh database (`tests/full_system.py`), 45 people/admin checks (`tests/people_admin.py`), plus rule, attack, login and wording suites.

Works on SQLite (laptop default) or the shared database via `DATABASE_URL`. Create tables with `python manage.py migrate`.

| Table | Key fields | Purpose |
| --- | --- | --- |
| `Student` | **cms_id (PK, 5 digits)**, profile, name, email (name+CMS+@mcs.nust.pk), phone (11 digits, 55…), program, semester, section | 20 seeded students |
| `Staff` | **staff_id (PK, STFnnnn)**, profile, name, email, phone, designation, department (assigned by admin), joined_on, is_active | 6 seeded staff, one per department; admin adds / assigns / removes |
| `Department` | **code (PK)**, name, office, email, phone, head | EE, CSE, IS, Library, Cafeteria, Labs |
| `Location` | **id (PK)**, name, department, kind, aliases | Campus places; each owned by one department (routing) |
| `UserProfile` | code (= CMS ID / Staff ID / admin1), name, role, department, trust_score | Common identity used by the rules engine. `trust_score` = student trust or staff rating (0–100) |
| `PortalAccount` | user (1:1), username (CMS ID / Staff ID), email, password_hash, **portal**, department, is_active, failed_attempts, locked_until, last_login_at | **Which portal each credential opens.** Login reads this row to identify the portal; a credential can never open another portal |
| `LoginAttempt` | username, portal, success, reason, ip, created_at | Audit log of every sign-in attempt |
| `django_session` | session_key, session_data, expire_date | Server-side sessions |
| `Issue` | issue_code, **general_title** ("AC fix"), **group_key** (place + problem), **report_count**, category, place_id, location, department, priority, score, priority_reason, reason, status, confidential, hazard, **resolved_by** (staff), reopen_count, round | One row per problem per place. Shown as "AC fix (total reports: 3)" |
| `Issue_reporters`, `Issue_affected_students` | issue ↔ user | Who reported; who is affected (reporters + "me too"). `report_count` = distinct affected students |
| `ReportEntry` | issue, user, raw_text, clean_text, triage (JSON from AI/keywords), flags, source, pending | Every report as written; `pending` = waiting for the student to pick a place |
| `ResolutionVote` | issue, voter, choice (confirm/dispute), weight, round, discounted | Affected students' votes after "resolved" or "no issue" |
| `TrustEvent` | user, delta, new_score, reason, issue_code, created_at | Every rating change and why (students and staff) |
| `IssueEvent` | issue, status, by, note, created_at | Status history |
| `Flag` | kind (injection/brigading/spam), issue, user_codes, note | Admin alerts |
| `AppSessionState` | issue_counter, user_counter | Counters for ISS-n / CONF-n / sN codes |

## Rules stored in the data

- **Grouping:** `group_key = "<place>[-<room>]:<problem>"`, e.g. `library:ac`, `cse-classroom-5:projector`. A new report with the
  same key joins the open issue and `report_count` goes up by one per new student (the same student again does not count).
- **Titles:** AC fix, Fan fix (separate from AC), WiFi fix, Projector fix, Computer fix, Electrical fix, Water / washroom fix,
  Cleaning needed, Furniture fix, Food quality issue, General fix; hazards: Fire hazard, Sparks / short circuit, Gas leak,
  Medical emergency, Electric shock risk.
- **Places:** owned by one department. Named off-campus places ("Hogwarts library", "Lab 99") are never routed. Classrooms exist only for CSE, EE and IS (`cse-classroom`, `ee-classroom`,
  `is-classroom`, room number kept). "Classroom 3" with no department asks the student to pick one.
- **Student rating:** staff "Checked: no issue" → reporters −10. If affected students dispute and win, it reopens and the −10 is refunded.
- **Staff rating:** fix verified by students +5; marked resolved but students say not fixed −10; "no issue" overturned −10; "no issue" confirmed +2.
- **Statuses:** open → in_progress → resolved / no_issue → (votes) verified / closed / reopened; spam.

## Sign-in

Students use their CMS ID or email; staff their Staff ID or email; admin `admin1` or `registrar@mcs.nust.pk`.
The system reads `PortalAccount` to detect the portal. Staff go straight to the queue of the department the admin
assigned (`/staff/library/` for Library). Reassigning takes effect at once; removed staff lose access at once.
All demo IDs, emails, phones and passwords: `docs/DEMO_ACCOUNTS.md`.

## Admin

- Complaints by state: **pending** (open, reopened, and reports waiting for a place), **active** (in progress, or resolved /
  no issue awaiting student confirmation), **completed** (verified, closed, spam). `GET /api/admin/complaints?state=...`
- Reports: `GET /api/admin/reports?period=weekly|monthly` downloads a CSV (add `&format=json` for JSON).
- Staff: `GET/POST /api/admin/staff`, `POST /api/admin/staff/<id>/assign {department}`, `POST /api/admin/staff/<id>/remove {restore?}`.
- Students: `GET /api/admin/students`. Departments: `GET /api/departments`.

## Fresh database

Migrations were rebuilt for this version: on an existing database delete `db.sqlite3` (or empty the shared database)
and run `python manage.py migrate`. The first request seeds all demo data.
