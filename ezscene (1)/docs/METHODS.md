# EzScene method reference

Generated from the docstrings in the code. Each line: what the method is for.

## Rules engine (`reportfix/engine.py`, class DjangoReportFixEngine)

| Method | Use |
| --- | --- |
| `_check_brigading` | 3+ brand-new accounts voting the same way within 10 minutes: flag them and count their votes as 0. |
| `_evaluate_votes` | confirm = 'staff are right' (fixed / really no issue); dispute = 'the problem is still there'. |
| `add_staff` | Admin adds staff: new Staff ID, PortalAccount and a one-time password (returned). |
| `admin_summary` | — |
| `admin_summary_with_source` | Admin briefing written by Gemini, or a rule-based summary if AI is unavailable. Returns (text, source). |
| `assign_staff` | Admin assigns a department; the staff member's next page opens that queue. |
| `brigading_alerts` | — |
| `can_manage` | Admin manages everything; staff only public issues of their own department. |
| `clarify_report` | The student picks the real place for a report that named none (rule 6), then it is filed. |
| `create_student` | New student with a random unused 5-digit CMS ID (primary key), email and phone. |
| `create_student_account` | — |
| `current_user` | — |
| `ensure_initialized` | Create the counters row and seed demo data the first time the database is used. |
| `get_user` | Look up a person by CMS ID, Staff ID or admin username (case-insensitive). |
| `join_me_too` | Student adds themselves to an issue ('me too'): counts once, raises crowd priority. |
| `remove_staff` | Admin removes (deactivates) or restores staff. History and ratings are kept. |
| `reset_all_data` | Judge tool: back to the demo seed. |
| `seed_default_data` | Wipe every table and load the demo seed (people from seed.seed_people, complaints from seed.seed_complaints). |
| `staff_update_status` | Staff/admin change status. resolved / no_issue open a vote round; no_issue and spam lower reporters' ratings. |
| `submit_report` | Student files a complaint: rate limit, triage, then confidential queue, place clarification, or filing. |
| `summary_facts` | Numbers for the admin summary (confidential cases appear only as a count). |
| `switch_user` | — |
| `tallies` | (confirm weight, dispute weight) for the current round, ignoring discounted brigading votes. |
| `trending_board` | Active public issues ranked by score plus recent activity (last 24 h). |
| `triage` | Turn report text into facts: AI (or keywords) extracts; code then applies the hazard, harassment, injection and place floors. Returns (triage, cleaned text, flags). |
| `visible_issues` | Issues a user may list in a view (board / mine / queue / all), active ones first, highest score first. |
| `visible_issues_for_current_user` | — |
| `vote_resolution` | Affected student confirms or disputes a fix / no-issue finding (rule 7). |
| `vote_threshold` | Weight needed to decide a vote: min(1.5, half the total weight of affected students). |

## Sign-in (`reportfix/auth.py`)

| Method | Use |
| --- | --- |
| `client_ip` | — |
| `create_account` | Create the credential row for a user. The portal is always derived from the user's role. Username = CMS ID (students), Staff ID (staff) or the admin username; email also works for sign-in. |
| `landing` | Where a user lands after sign-in: (portal, path). Staff go straight to their assigned department. |
| `login` | — |
| `session_user` | — |
| `start_session` | — |

## AI (`reportfix/ai.py`)

| Method | Use |
| --- | --- |
| `summarize` | — |
| `triage` | candidates: [{'id','place_id','title'}] open public issues. Returns the Triage dict. |

## Detectors + keyword fallback (`reportfix/classify.py`)

| Method | Use |
| --- | --- |
| `detect_category` | — |
| `detect_confidential` | — |
| `detect_hazard` | — |
| `detect_injection` | — |
| `fallback_triage` | Keyword classifier: used when Gemini is unavailable. Same output shape as the AI. |
| `is_shouting` | — |
| `strip_injection` | — |
| `tone_clean` | Remove tone so priority can't be shouted up: lowercase, no '!!!', no urgency words. |

## Campus map (`reportfix/places.py`)

| Method | Use |
| --- | --- |
| `match_place` | Deterministic place matcher used by the fallback classifier. |
| `mentions_classroom` | — |
| `off_campus_mention` | — |
| `place_key_and_name` | Classrooms keep their room number: ('cse-classroom-5', 'CSE Classroom 5'). |
| `room_number` | — |
| `suggest_places` | — |
| `unknown_numbered_mention` | A numbered place that is not on the campus map, e.g. 'Lab 99' or 'Room 404'. |

## Demo data (`reportfix/seed.py`)

| Method | Use |
| --- | --- |
| `at` | Run code as if it happened `days_ago` days back (complaint history for reports). |
| `phone` | — |
| `seed_complaints` | — |
| `seed_people` | — |
| `student_email` | — |
| `unique_cms` | — |
| `unique_staff_id` | — |

## Web pages (`reportfix/ui.py`)

| Method | Use |
| --- | --- |
| `admin_home` | Complaints by state (pending / active / completed), headline numbers and alerts. |
| `admin_reports_page` | Weekly / monthly report downloads (CSV is produced by api.admin_reports). |
| `admin_staff_page` | List, add, assign and remove staff. |
| `admin_students_page` | — |
| `back` | Return to the page the form was posted from (only same-site paths). |
| `board` | Public campus board (never confidential) + trending. |
| `current_user` | Signed-in user, or in judge mode the first demo student. None means: show the login page. |
| `decorate` | Attach per-viewer fields the templates need (affected?, my vote, can manage, vote tallies). |
| `flash` | Queue a one-time message for the next page (stored in the session). |
| `home` | `/`: send the visitor to their portal. |
| `issue_join` | — |
| `issue_page` | — |
| `issue_status` | — |
| `issue_vote` | — |
| `judge_new_student` | — |
| `judge_reset` | — |
| `judge_switch` | Judge mode: become any user in one click (no password), as the hackathon rules require. |
| `landing_url` | Portal home for a user; staff land on their department queue. |
| `login_view` | Sign-in with CMS ID / Staff ID / email. The portal is detected from the PortalAccount row. |
| `logout_view` | — |
| `page` | Decorator: require a signed-in user (optionally with one of `roles`) and pass it to the view. |
| `person_page` | Why someone has their rating: every change with its reason (rule 8). |
| `render_page` | Render with the shared layout context (nav, judge bar, flash messages). |
| `report_clarify` | — |
| `report_submit` | Run a report through triage + rules; keep the outcome for the next page. |
| `staff_queue` | Department queue. The department always comes from the database (admin assignment), never the URL. |
| `student_home` | Report form, the result of the last report, my issues (reported or joined) and unclear reports. |
| `with_issue_prefetch` | One query each for affected students, reports and their authors (avoids N+1 on lists). |

## JSON API (`reportfix/api.py`)

| Method | Use |
| --- | --- |
| `_current` | The signed-in user from the server-side session. Never trusts a client-sent id. |
| `admin_accounts` | — |
| `admin_complaints` | — |
| `admin_flags` | — |
| `admin_reports` | — |
| `admin_staff` | — |
| `admin_staff_assign` | — |
| `admin_staff_remove` | — |
| `admin_students` | — |
| `admin_summary` | — |
| `auth_login` | — |
| `auth_logout` | — |
| `auth_me` | — |
| `can_view` | — |
| `clarify` | — |
| `departments` | — |
| `health` | — |
| `issue_detail` | — |
| `issue_json` | — |
| `issues` | — |
| `join` | — |
| `login_required_json` | — |
| `places` | — |
| `reports` | — |
| `reset` | — |
| `session` | — |
| `status` | — |
| `trending` | — |
| `user_json` | — |
| `user_trust` | — |
| `users` | — |
| `vote` | — |

## Database tables (`reportfix/models.py`)

| Table | Use |
| --- | --- |
| `AppSessionState` | Counters + the CLI's active user (the web app uses a cookie per browser instead). |
| `Department` | The six departments that receive complaints. |
| `Flag` | Flag(id, kind, issue, user_codes, note, created_at) |
| `Issue` | Issue(id, issue_code, title, general_title, group_key, report_count, resolved_by, category, place_id, location, department, priority, score, priority_reason, reason, status, confidential, hazard, reopen_count, round, created_at, updated_at) |
| `IssueEvent` | IssueEvent(id, issue, status, by, note, created_at) |
| `Location` | Campus places; each belongs to one department (routing). |
| `LoginAttempt` | Audit log of every sign-in attempt (success or failure). |
| `PortalAccount` | Login credentials, and which portal each credential may sign in to. |
| `ReportEntry` | ReportEntry(id, issue, user, raw_text, clean_text, triage, flags, source, pending, created_at) |
| `ResolutionVote` | ResolutionVote(id, issue, voter, choice, weight, round, discounted, created_at) |
| `Staff` | Department staff. Staff ID is the primary key and the login username. |
| `Student` | Student record. CMS ID (5 digits) is the primary key and the login username. |
| `TrustEvent` | TrustEvent(id, user, created_at, delta, new_score, reason, issue_code) |
| `UserProfile` | UserProfile(id, code, name, role, department, trust_score, created_at) |
