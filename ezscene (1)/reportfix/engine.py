"""ReportFix rules engine. AI supplies facts (reportfix.ai); every decision here is plain code."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from reportfix import ai
from reportfix.classify import (
    detect_confidential,
    detect_hazard,
    detect_injection,
    fallback_triage,
    is_shouting,
    strip_injection,
    tone_clean,
)
from reportfix.models import (
    CONFIDENTIAL_QUEUE,
    DEPARTMENTS,
    AppSessionState,
    Department,
    Flag,
    Location,
    Staff,
    Student,
    LoginAttempt,
    PortalAccount,
    Issue,
    IssueEvent,
    ReportEntry,
    ResolutionVote,
    TrustEvent,
    UserProfile,
)
from reportfix.places import (PLACE_BY_ID, off_campus_mention, place_key_and_name, suggest_places,
                              unknown_numbered_mention)

# Trust changes (rule 8) — shown to users with the reason.
TRUST_VERIFIED_REPORT = 5
TRUST_VOTE_RIGHT = 3
TRUST_VOTE_WRONG = -2
TRUST_SPAM = -15
TRUST_INJECTION = -10
TRUST_BRIGADING = -5

TRUST_NO_ISSUE = -10            # staff checked and found no issue
TRUST_NO_ISSUE_REFUND = 10      # ...but students proved the problem was real
STAFF_FIX_VERIFIED = 5          # staff rating: students confirmed the fix
STAFF_FIX_DISPUTED = -10        # staff rating: marked resolved, students say not fixed
STAFF_NO_ISSUE_CONFIRMED = 2
STAFF_NO_ISSUE_OVERTURNED = -10

# One general title per kind of problem: every report of the same problem at the same place
# lands on one issue, shown as e.g. "AC fix (total reports: 3)".
GENERAL_TITLES = {
    "ac": "AC fix", "fan": "Fan fix", "wifi": "WiFi fix", "projector": "Projector fix", "computers": "Computer fix",
    "electrical": "Electrical fix", "water": "Water / washroom fix", "cleanliness": "Cleaning needed",
    "furniture": "Furniture fix", "food": "Food quality issue", "safety": "Safety hazard",
    "harassment": "Confidential welfare case", "other": "General fix",
}
HAZARD_TITLES = {"fire": "Fire hazard", "sparks": "Sparks / short circuit", "gas": "Gas leak",
                 "medical": "Medical emergency", "electrical": "Electric shock risk"}


def general_title(category: str, hazard: str | None) -> str:
    return HAZARD_TITLES.get(hazard or "", "") or GENERAL_TITLES.get(category, "General fix")


SPAM_MAX_REPORTS_PER_MINUTE = 10
BRIGADE_MIN_NEW_ACCOUNTS = 3
BRIGADE_WINDOW_MINUTES = 10


class DjangoReportFixEngine:
    # ------------------------------------------------------------------ setup
    def ensure_initialized(self) -> AppSessionState:
        """Create the counters row and seed demo data the first time the database is used."""
        state, _ = AppSessionState.objects.get_or_create(singleton_id=1)
        if not UserProfile.objects.exists():
            self.seed_default_data()
            state.refresh_from_db()
        return state

    @transaction.atomic
    def seed_default_data(self) -> None:
        """Wipe every table and load the demo seed (people from seed.seed_people, complaints from seed.seed_complaints)."""
        from reportfix import seed
        for model in (Flag, IssueEvent, ResolutionVote, ReportEntry, Issue, TrustEvent, LoginAttempt, PortalAccount,
                      Student, Staff, Location, UserProfile, Department):
            model.objects.all().delete()
        state, _ = AppSessionState.objects.get_or_create(singleton_id=1)
        state.issue_counter = 0
        state.user_counter = 0
        state.save()
        people = seed.seed_people()
        state.current_user_code = people["students"][0]
        state.save(update_fields=["current_user_code"])
        seed.seed_complaints(self, people)

    def reset_all_data(self) -> str:
        """Judge tool: back to the demo seed."""
        self.seed_default_data()
        return "All data reset to the seeded baseline (20 students, 6 staff, 1 admin, demo complaints)."

    # ------------------------------------------------------------------ users
    def get_user(self, code: str) -> Optional[UserProfile]:
        """Look up a person by CMS ID, Staff ID or admin username (case-insensitive)."""
        return UserProfile.objects.filter(code__iexact=(code or "").strip()).first()

    def current_user(self) -> UserProfile:  # CLI only
        state = self.ensure_initialized()
        return self.get_user(state.current_user_code) or UserProfile.objects.filter(role="student").first()

    def switch_user(self, identifier: str) -> Tuple[bool, str]:  # CLI only
        state = self.ensure_initialized()
        ident = identifier.strip()
        user = self.get_user(ident) or UserProfile.objects.filter(name__icontains=ident).first()
        if not user:
            return False, f"User '{identifier}' not found. Use 'users' to list accounts."
        state.current_user_code = user.code
        state.save(update_fields=["current_user_code"])
        return True, f"Switched to {user.name} ({user.code}) | Role: {user.role}"

    @transaction.atomic
    def create_student(self, name: str = "") -> UserProfile:
        """New student with a random unused 5-digit CMS ID (primary key), email and phone."""
        import random
        from reportfix import seed
        from reportfix.auth import create_account
        self.ensure_initialized()
        state = AppSessionState.objects.select_for_update().get(singleton_id=1)
        state.user_counter += 1
        rng = random.Random()
        clean_name = re.sub(r"\s+", " ", (name or "").strip())[:60] or f"New Student {state.user_counter}"
        cms = seed.unique_cms(rng, set(UserProfile.objects.values_list("code", flat=True)))
        student = UserProfile.objects.create(code=cms, name=clean_name, role=UserProfile.ROLE_STUDENT, trust_score=50.0)
        email = seed.student_email(re.sub(r"[^A-Za-z ]", "", clean_name) or "student", cms)
        Student.objects.create(cms_id=cms, profile=student, name=clean_name, email=email, phone=seed.phone(rng),
                               program=rng.choice(seed.PROGRAMS), semester=1, section=rng.choice("ABC"))
        TrustEvent.objects.create(user=student, delta=0, new_score=50.0,
                                  reason="Baseline trust for every new student (new accounts count 0.5 for 30 minutes)")
        _acct, raw_password = create_account(student, email=email)  # random password, shown once
        student.new_password = raw_password
        state.current_user_code = cms
        state.save(update_fields=["user_counter", "current_user_code"])
        return student

    # ------------------------------------------------------------------ admin: staff management
    @transaction.atomic
    def add_staff(self, name: str, department: Optional[str], designation: str = "", email: str = "", phone: str = ""):
        """Admin adds staff: new Staff ID, PortalAccount and a one-time password (returned)."""
        import random
        from reportfix import seed
        from reportfix.auth import create_account
        name = re.sub(r"\s+", " ", (name or "").strip())[:120]
        if not name:
            raise ValueError("Staff name is required.")
        if department and not Department.objects.filter(code=department).exists():
            raise ValueError(f"Unknown department '{department}'.")
        rng = random.Random()
        sid = seed.unique_staff_id(rng, set())
        email = (email or f"{name.lower().replace(' ', '.')}.{sid.lower()}@{seed.EMAIL_DOMAIN}").strip().lower()
        if Staff.objects.filter(email=email).exists() or PortalAccount.objects.filter(email=email).exists():
            raise ValueError("That email is already used.")
        phone = phone if re.fullmatch(r"\d{11}", phone or "") else seed.phone(rng)
        prof = UserProfile.objects.create(code=sid, name=name, role=UserProfile.ROLE_STAFF, department=department or None,
                                          trust_score=50.0)
        staff = Staff.objects.create(staff_id=sid, profile=prof, name=name, email=email, phone=phone,
                                     designation=designation or "Department staff", department_id=department or None,
                                     joined_on=timezone.localdate())
        TrustEvent.objects.create(user=prof, delta=0, new_score=50.0, reason="Baseline staff rating")
        _acct, raw = create_account(prof, email=email)
        return staff, raw

    @transaction.atomic
    def assign_staff(self, staff_id: str, department: Optional[str]) -> Staff:
        """Admin assigns a department; the staff member's next page opens that queue."""
        staff = Staff.objects.select_related("profile").filter(staff_id=staff_id).first()
        if not staff:
            raise LookupError("Staff not found.")
        if department and not Department.objects.filter(code=department).exists():
            raise ValueError(f"Unknown department '{department}'.")
        staff.department_id = department or None
        staff.save(update_fields=["department"])
        staff.profile.department = department or None
        staff.profile.save(update_fields=["department"])
        PortalAccount.objects.filter(user=staff.profile).update(department=department or None)
        return staff

    @transaction.atomic
    def remove_staff(self, staff_id: str, restore: bool = False) -> Staff:
        """Admin removes (deactivates) or restores staff. History and ratings are kept."""
        staff = Staff.objects.select_related("profile").filter(staff_id=staff_id).first()
        if not staff:
            raise LookupError("Staff not found.")
        staff.is_active = restore
        staff.save(update_fields=["is_active"])
        PortalAccount.objects.filter(user=staff.profile).update(is_active=restore)
        return staff

    def create_student_account(self, name: str) -> Tuple[bool, str]:  # CLI compat
        s = self.create_student(name)
        return True, (f"Created & switched to new student {s.name} ({s.code}) with baseline trust 50. "
                      f"Login: username {s.code}, password {s.new_password} (shown once).")

    # ------------------------------------------------------------------ triage
    def _candidates(self) -> List[Issue]:
        return list(
            Issue.objects.filter(confidential=False, status__in=Issue.ACTIVE_STATUSES).order_by("-updated_at")[:40]
        )

    def triage(self, text: str, use_ai: bool = True, force_place_id: Optional[str] = None) -> Tuple[Dict, str, List[str]]:
        """Turn report text into facts: AI (or keywords) extracts; code then applies the hazard, harassment,
        injection and place floors. Returns (triage, cleaned text, flags)."""
        flags: List[str] = []
        if is_shouting(text):
            flags.append("shouting")
        clean = tone_clean(text)
        candidates = self._candidates()
        tri: Optional[Dict] = None
        if use_ai and settings.GEMINI_API_KEY:
            try:
                hint = f"\n(The reporter has confirmed the place is: {PLACE_BY_ID[force_place_id]['name']})" if force_place_id else ""
                tri = ai.triage(clean + hint, [{"id": c.issue_code, "place_id": c.place_id, "title": c.title} for c in candidates])
            except Exception:
                tri = None  # ai.STATUS keeps the error; fall back to keywords
        if tri is None:
            tri = fallback_triage(clean)
            ai.STATUS["last_source"] = "fallback"

        # Code-side floors: a fire is never missed, harassment never leaks, commands never obeyed.
        data_only = strip_injection(text)
        kw_hazard = detect_hazard(data_only)
        if kw_hazard and not tri["hazard"]:
            tri["hazard"] = kw_hazard
            tri["reason"] = (tri["reason"] + f" Safety keyword '{kw_hazard}' detected by code.").strip()
        if detect_confidential(data_only):
            tri["confidential"] = True
        if tri["confidential"]:
            tri["category"] = "harassment"
        tri["injection"] = bool(tri["injection"] or detect_injection(text))

        # Place validation (rule 6): only real campus places, no guessing.
        if force_place_id:
            tri["place_id"] = force_place_id
        if tri["place_id"] not in PLACE_BY_ID:
            tri["place_id"] = None
        unknown = unknown_numbered_mention(data_only, tri["place_id"]) or off_campus_mention(data_only)
        if unknown and not force_place_id:
            tri["place_id"] = None
            tri["place_mention"] = unknown

        # Duplicate validation (rule 2): only an active public issue at the SAME place.
        dup = tri.get("same_as_issue_id")
        valid = {c.issue_code: c for c in candidates}
        if dup and (dup not in valid or valid[dup].place_id != tri["place_id"]):
            dup = None
        tri["same_as_issue_id"] = dup  # grouping itself is by place + problem (see _file_public)
        if tri["injection"]:
            flags.append("injection")
        return tri, clean, flags

    # ------------------------------------------------------------------ reports
    def _next_issue_code(self, prefix: str) -> str:
        state = AppSessionState.objects.select_for_update().get(singleton_id=1)
        state.issue_counter += 1
        state.save(update_fields=["issue_counter"])
        return f"{prefix}-{state.issue_counter}"

    def _event(self, issue: Issue, status: str, by: str, note: str = "") -> None:
        IssueEvent.objects.create(issue=issue, status=status, by=by[:120], note=note[:255])

    @transaction.atomic
    def submit_report(self, user_code: str, raw_text: str, use_ai: bool = True) -> Dict:
        """Student files a complaint: rate limit, triage, then confidential queue, place clarification, or filing."""
        self.ensure_initialized()
        user = self.get_user(user_code)
        if not user or user.role != UserProfile.ROLE_STUDENT:
            return {"ok": False, "outcome": "rejected", "message": "Only students can submit reports."}
        text = (raw_text or "").strip()[:2000]
        if len(text) < 4:
            return {"ok": False, "outcome": "rejected", "message": "Please describe the problem in a few words."}

        recent = ReportEntry.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(seconds=60)).count()
        if recent >= SPAM_MAX_REPORTS_PER_MINUTE:
            Flag.objects.create(kind="spam", user_codes=[user.code],
                                note=f"{user.name} sent more than {SPAM_MAX_REPORTS_PER_MINUTE} reports in one minute")
            return {"ok": False, "outcome": "rejected",
                    "message": "Too many reports in one minute. Please wait before reporting again."}

        tri, clean, flags = self.triage(text, use_ai=use_ai)
        report = ReportEntry.objects.create(user=user, raw_text=text, clean_text=clean, triage=tri,
                                            flags=flags, source=tri["source"])
        if tri["injection"]:
            self._penalize_injection(user, report, tri)

        if tri["confidential"]:
            return self._file_confidential(user, report, tri)
        if not tri["place_id"]:
            report.pending = True
            report.save(update_fields=["pending"])
            mention = tri.get("place_mention")
            msg = (f"'{mention}' is not a place on the campus map." if mention else "No campus place was named.") + \
                " Please pick the right place so we can route it (no guessing)."
            return {"ok": False, "outcome": "needs_clarification", "report": report, "clarification": msg,
                    "message": msg, "suggestions": suggest_places(mention, text), "injection_ignored": tri["injection"]}
        return self._file_public(user, report, tri)

    @transaction.atomic
    def clarify_report(self, user_code: str, report_id: int, place_id: str, use_ai: bool = True) -> Dict:
        """The student picks the real place for a report that named none (rule 6), then it is filed."""
        user = self.get_user(user_code)
        report = ReportEntry.objects.filter(id=report_id).first()
        if not report or not user or report.user_id != user.id:
            return {"ok": False, "outcome": "rejected", "message": "Report not found.", "status": 404}
        if not report.pending:
            return {"ok": False, "outcome": "rejected", "message": "This report is already filed.", "status": 400}
        if place_id not in PLACE_BY_ID:
            return {"ok": False, "outcome": "rejected", "message": "Unknown place.", "status": 400}
        tri, clean, flags = self.triage(report.raw_text, use_ai=use_ai, force_place_id=place_id)
        tri["injection"] = report.triage.get("injection", False) or tri["injection"]
        report.triage, report.clean_text, report.pending = tri, clean, False
        report.save(update_fields=["triage", "clean_text", "pending"])
        if tri["confidential"]:
            return self._file_confidential(user, report, tri)
        return self._file_public(user, report, tri)

    def _penalize_injection(self, user: UserProfile, report: ReportEntry, tri: Dict) -> None:
        user.adjust_trust(TRUST_INJECTION, "A report tried to command the system (prompt injection); it was ignored and treated as plain data")
        if tri["confidential"]:
            Flag.objects.create(kind="injection", note="A confidential report contained instruction-like text (ignored)")
        else:
            Flag.objects.create(kind="injection", user_codes=[user.code],
                                note=f"{user.name}: \"{report.raw_text[:140]}\"")

    def _file_confidential(self, user: UserProfile, report: ReportEntry, tri: Dict) -> Dict:
        place = PLACE_BY_ID.get(tri["place_id"] or "")
        issue = Issue.objects.create(
            issue_code=self._next_issue_code("CONF"),
            title="Confidential welfare case",
            general_title="Confidential welfare case",
            group_key=f"confidential:{report.id}",
            category="harassment",
            place_id=tri["place_id"] or "",
            location=place["name"] if place else "Not stated",
            department=CONFIDENTIAL_QUEUE,
            reason="Harassment / ragging report: admin-only queue, reporter identity hidden.",
            status=Issue.STATUS_OPEN,
            confidential=True,
            hazard=tri["hazard"] or "",
        )
        issue.reporters.add(user)
        issue.affected_students.add(user)
        issue.refresh_count()
        report.issue = issue
        report.save(update_fields=["issue"])
        self._event(issue, Issue.STATUS_OPEN, "Hidden", "Confidential report filed")
        issue.recompute_priority()
        return {"ok": True, "outcome": "created", "issue": issue, "report": report, "injection_ignored": tri["injection"],
                "message": f"Confidential report {issue.issue_code} filed. Only the admin can see it, and your name is never shown."}

    def _file_public(self, user: UserProfile, report: ReportEntry, tri: Dict) -> Dict:
        place = PLACE_BY_ID[tri["place_id"]]
        place_key, location = place_key_and_name(place["id"], report.raw_text)
        problem = tri["hazard"] or tri["category"]
        group_key = f"{place_key}:{problem}"
        active = Issue.objects.filter(confidential=False, status__in=Issue.ACTIVE_STATUSES)
        # Same problem + same place = same issue (rule 2), however it was worded.
        issue = active.filter(group_key=group_key).order_by("created_at").first()
        if issue is None and tri.get("same_as_issue_id"):  # AI matched by meaning; only within the same place
            issue = active.filter(issue_code=tri["same_as_issue_id"], group_key__startswith=f"{place_key}:").first()
        now = timezone.now()
        if issue:
            already = issue.affected_students.filter(pk=user.pk).exists()
            issue.reporters.add(user)
            issue.affected_students.add(user)
            if tri["hazard"] and not issue.hazard:
                issue.hazard = tri["hazard"]
            issue.updated_at = now
            issue.save(update_fields=["hazard", "updated_at"])
            issue.refresh_count()
            report.issue = issue
            report.save(update_fields=["issue"])
            self._event(issue, issue.status, user.name,
                        "Same student reported again (count unchanged)" if already else
                        f"Another student reported the same problem (total reports: {issue.report_count})")
            if issue.status in Issue.VOTING_STATUSES:
                self._cast_vote(issue, user, ResolutionVote.CHOICE_DISPUTE, auto=True)
                issue.refresh_from_db()
            issue.recompute_priority()
            note = " You had already reported this, so the count stays the same." if already else ""
            return {"ok": True, "outcome": "merged", "issue": issue, "report": report, "injection_ignored": tri["injection"],
                    "message": f"Same problem as {issue.issue_code}: {issue.display_title}, {issue.location} → "
                               f"{issue.department}.{note}"}

        title = general_title(tri["category"], tri["hazard"])
        issue = Issue.objects.create(
            issue_code=self._next_issue_code("ISS"),
            title=title,
            general_title=title,
            group_key=group_key,
            category=tri["category"],
            place_id=place["id"],
            location=location,
            department=place["dept"],
            reason=f"{tri['reason']} Routed to {place['dept']} (owner of {place['name']}).".strip()[:255],
            status=Issue.STATUS_OPEN,
            hazard=tri["hazard"] or "",
        )
        issue.reporters.add(user)
        issue.affected_students.add(user)
        issue.refresh_count()
        report.issue = issue
        report.save(update_fields=["issue"])
        self._event(issue, Issue.STATUS_OPEN, user.name, "Reported")
        issue.recompute_priority()
        return {"ok": True, "outcome": "created", "issue": issue, "report": report, "injection_ignored": tri["injection"],
                "message": f"Created {issue.issue_code}: {issue.display_title}, {issue.location} → {issue.department} "
                           f"[{issue.priority}]. Reason: {issue.reason}"}

    # ------------------------------------------------------------------ community
    @transaction.atomic
    def join_me_too(self, user_code: str, issue_code: str) -> Tuple[bool, str]:
        """Student adds themselves to an issue ('me too'): counts once, raises crowd priority."""
        user = self.get_user(user_code)
        if not user or user.role != UserProfile.ROLE_STUDENT:
            return False, "Only students can join an issue ('me too')."
        issue = Issue.objects.filter(issue_code__iexact=issue_code.strip(), confidential=False).first()
        if not issue:
            return False, f"Public issue '{issue_code}' not found."
        if issue.status in (Issue.STATUS_SPAM, Issue.STATUS_VERIFIED, Issue.STATUS_CLOSED):
            return False, f"{issue.issue_code} is closed ({issue.status})."
        if issue.affected_students.filter(pk=user.pk).exists():
            return False, f"You are already affected on {issue.issue_code}."
        issue.affected_students.add(user)
        issue.updated_at = timezone.now()
        issue.save(update_fields=["updated_at"])
        issue.refresh_count()
        self._event(issue, issue.status, user.name, f"Joined (me too), total reports: {issue.report_count}")
        issue.recompute_priority()
        return True, f"Added you to {issue.issue_code}: {issue.display_title}."

    def can_manage(self, actor: UserProfile, issue: Issue) -> bool:
        """Admin manages everything; staff only public issues of their own department."""
        if actor.role == UserProfile.ROLE_ADMIN:
            return True
        return actor.role == UserProfile.ROLE_STAFF and not issue.confidential and issue.department == actor.department

    @transaction.atomic
    def staff_update_status(self, user_code: str, issue_code: str, new_status: str, note: str = "") -> Tuple[bool, str]:
        """Staff/admin change status. resolved / no_issue open a vote round; no_issue and spam lower reporters' ratings."""
        actor = self.get_user(user_code)
        if not actor or actor.role not in (UserProfile.ROLE_STAFF, UserProfile.ROLE_ADMIN):
            return False, "Only department staff or admin can update issue status."
        issue = Issue.objects.filter(issue_code__iexact=issue_code.strip()).first()
        if not issue or (issue.confidential and actor.role != UserProfile.ROLE_ADMIN):
            return False, f"Issue '{issue_code}' not found."
        if not self.can_manage(actor, issue):
            return False, f"Access denied: {issue.issue_code} belongs to {issue.department}, not {actor.department}."
        st = new_status.strip().lower()
        if st not in (Issue.STATUS_OPEN, Issue.STATUS_IN_PROGRESS, Issue.STATUS_RESOLVED, Issue.STATUS_NO_ISSUE, Issue.STATUS_SPAM):
            return False, "Status must be one of: open, in_progress, resolved, no_issue, spam."
        if issue.status in (Issue.STATUS_VERIFIED, Issue.STATUS_SPAM, Issue.STATUS_CLOSED):
            return False, f"{issue.issue_code} is closed ({issue.status})."
        msg_extra = ""
        if st in (Issue.STATUS_RESOLVED, Issue.STATUS_NO_ISSUE):
            issue.round += 1
            issue.resolved_by = actor if actor.role == UserProfile.ROLE_STAFF else None
            msg_extra = f" Affected students can now confirm or dispute (round {issue.round})."
        if st == Issue.STATUS_NO_ISSUE:
            code = "" if issue.confidential else issue.issue_code
            for reporter in issue.reporters.all():
                reporter.adjust_trust(TRUST_NO_ISSUE, f"Staff checked {code or 'your report'} and found no issue", code)
            msg_extra = (" Reporters' ratings fell; if affected students dispute and win, the issue reopens, "
                         "their ratings are restored and the staff rating falls.")
        if st == Issue.STATUS_SPAM:
            for reporter in issue.reporters.all():
                reason = ("A confidential report was marked as fake by admin" if issue.confidential
                          else f"Staff marked report {issue.issue_code} as spam / fake")
                reporter.adjust_trust(TRUST_SPAM, reason, "" if issue.confidential else issue.issue_code)
            Flag.objects.create(kind="spam", issue=None if issue.confidential else issue,
                                user_codes=[] if issue.confidential else list(issue.reporters.values_list("code", flat=True)),
                                note=f"{actor.name} marked {issue.issue_code} as spam")
        issue.status = st
        issue.updated_at = timezone.now()
        issue.save(update_fields=["status", "round", "resolved_by", "updated_at"])
        self._event(issue, st, actor.name, note or (msg_extra.strip() if msg_extra else ""))
        issue.recompute_priority()
        return True, f"Updated {issue.issue_code} to '{st}'.{msg_extra}"

    def _round_votes(self, issue: Issue) -> List[ResolutionVote]:
        return list(issue.votes.filter(round=issue.round).select_related("voter"))

    def vote_threshold(self, issue: Issue) -> float:
        """Weight needed to decide a vote: min(1.5, half the total weight of affected students)."""
        discounted = set(issue.votes.filter(round=issue.round, discounted=True).values_list("voter_id", flat=True))
        total = sum(u.weight() for u in issue.affected_students.all() if u.id not in discounted)
        return round(max(0.1, min(1.5, 0.5 * total)), 2)

    def tallies(self, issue: Issue) -> Tuple[float, float]:
        """(confirm weight, dispute weight) for the current round, ignoring discounted brigading votes."""
        votes = [v for v in self._round_votes(issue) if not v.discounted]
        wc = round(sum(v.weight for v in votes if v.choice == ResolutionVote.CHOICE_CONFIRM), 2)
        wd = round(sum(v.weight for v in votes if v.choice == ResolutionVote.CHOICE_DISPUTE), 2)
        return wc, wd

    @transaction.atomic
    def vote_resolution(self, user_code: str, issue_code: str, choice: str) -> Tuple[bool, str]:
        """Affected student confirms or disputes a fix / no-issue finding (rule 7)."""
        voter = self.get_user(user_code)
        if not voter or voter.role != UserProfile.ROLE_STUDENT:
            return False, "Only affected students can vote on a fix."
        issue = Issue.objects.filter(issue_code__iexact=issue_code.strip()).first()
        if not issue or (issue.confidential and not issue.affected_students.filter(pk=voter.pk).exists()):
            return False, f"Issue '{issue_code}' not found."
        if issue.status not in Issue.VOTING_STATUSES:
            return False, f"Voting opens only after staff mark it resolved or 'no issue' (current: {issue.status})."
        if not issue.affected_students.filter(pk=voter.pk).exists():
            return False, f"Denied: only students affected by {issue.issue_code} (reporters and 'me too') may vote."
        ch = choice.strip().lower()
        if ch not in (ResolutionVote.CHOICE_CONFIRM, ResolutionVote.CHOICE_DISPUTE):
            return False, "Vote must be 'confirm' or 'dispute'."
        return self._cast_vote(issue, voter, ch)

    def _cast_vote(self, issue: Issue, voter: UserProfile, choice: str, auto: bool = False) -> Tuple[bool, str]:
        weight = voter.weight()
        vote, _ = ResolutionVote.objects.update_or_create(
            issue=issue, voter=voter, round=issue.round,
            defaults={"choice": choice, "weight": weight, "created_at": timezone.now()},
        )
        self._event(issue, issue.status, voter.name if not issue.confidential else "Hidden",
                    f"{'Reported again, counted as ' if auto else 'Voted '}{choice} (weight {weight:.2f})")
        brigade_msg = self._check_brigading(issue)
        outcome = self._evaluate_votes(issue)
        wc, wd = self.tallies(issue)
        return True, (f"Recorded '{choice}' (weight {weight:.2f}) on {issue.issue_code}. "
                      f"Confirm {wc:.2f} vs dispute {wd:.2f}, needed {self.vote_threshold(issue):.2f}.{brigade_msg}{outcome}")

    def _check_brigading(self, issue: Issue) -> str:
        """3+ brand-new accounts voting the same way within 10 minutes: flag them and count their votes as 0."""
        window = timezone.now() - timedelta(minutes=BRIGADE_WINDOW_MINUTES)
        msg = ""
        for choice in (ResolutionVote.CHOICE_CONFIRM, ResolutionVote.CHOICE_DISPUTE):
            suspects = [v for v in self._round_votes(issue)
                        if v.choice == choice and v.created_at >= window and v.voter.is_new_account()]
            if len(suspects) >= BRIGADE_MIN_NEW_ACCOUNTS:
                fresh = [v for v in suspects if not v.discounted]
                for v in fresh:
                    v.discounted, v.weight = True, 0.0
                    v.save(update_fields=["discounted", "weight"])
                    v.voter.adjust_trust(TRUST_BRIGADING, f"Part of a burst of brand-new accounts voting together on {issue.issue_code}",
                                         issue.issue_code)
                if fresh:
                    Flag.objects.create(kind="brigading", issue=issue, user_codes=[v.voter.code for v in suspects],
                                        note=f"{len(suspects)} brand-new accounts voted '{choice}' on {issue.issue_code} "
                                             f"within {BRIGADE_WINDOW_MINUTES} min; their votes count 0")
                    msg = f" Brigading detected: {len(suspects)} brand-new accounts voted together; their votes count 0."
        return msg

    def _evaluate_votes(self, issue: Issue) -> str:
        """confirm = 'staff are right' (fixed / really no issue); dispute = 'the problem is still there'."""
        wc, wd = self.tallies(issue)
        need = self.vote_threshold(issue)
        votes = [v for v in self._round_votes(issue) if not v.discounted]
        code = "" if issue.confidential else issue.issue_code
        label = code or "a confidential case"
        staff = issue.resolved_by
        was_no_issue = issue.status == Issue.STATUS_NO_ISSUE
        if wd > wc and wd >= need:
            issue.status = Issue.STATUS_REOPENED
            issue.reopen_count += 1
            issue.updated_at = timezone.now()
            issue.save(update_fields=["status", "reopen_count", "updated_at"])
            self._event(issue, Issue.STATUS_REOPENED, "Crowd", f"Disputes {wd:.2f} beat confirms {wc:.2f} (needed {need:.2f})")
            for v in votes:
                if v.choice == ResolutionVote.CHOICE_DISPUTE:
                    v.voter.adjust_trust(TRUST_VOTE_RIGHT, f"Your dispute on {label} was backed by the crowd (reopened)", code)
                else:
                    v.voter.adjust_trust(TRUST_VOTE_WRONG, f"You sided with staff on {label}, but the crowd disputed it", code)
            if was_no_issue:
                for reporter in issue.reporters.all():
                    reporter.adjust_trust(TRUST_NO_ISSUE_REFUND, f"Students confirmed the problem on {label} is real (rating restored)", code)
                if staff:
                    staff.adjust_trust(STAFF_NO_ISSUE_OVERTURNED, f"Said 'no issue' on {label}, but affected students confirmed the problem", code)
            elif staff:
                staff.adjust_trust(STAFF_FIX_DISPUTED, f"Marked {label} resolved, but affected students say it is not fixed", code)
            issue.recompute_priority()
            return " Enough credible disputes: issue REOPENED" + (" and the staff rating fell." if staff else ".")
        if wc > 0 and wc >= wd and wc >= need:
            issue.status = Issue.STATUS_CLOSED if was_no_issue else Issue.STATUS_VERIFIED
            issue.updated_at = timezone.now()
            issue.save(update_fields=["status", "updated_at"])
            self._event(issue, issue.status, "Crowd", f"Confirms {wc:.2f} vs disputes {wd:.2f} (needed {need:.2f})")
            for v in votes:
                if v.choice == ResolutionVote.CHOICE_CONFIRM:
                    v.voter.adjust_trust(TRUST_VOTE_RIGHT, f"Your vote on {label} matched the crowd", code)
                else:
                    v.voter.adjust_trust(TRUST_VOTE_WRONG, f"You disputed {label}, but the crowd agreed with staff", code)
            if was_no_issue:
                if staff:
                    staff.adjust_trust(STAFF_NO_ISSUE_CONFIRMED, f"Students agreed there was no issue on {label}", code)
                issue.recompute_priority()
                return " Students agreed there was no issue: CLOSED."
            for reporter in issue.reporters.all():
                reporter.adjust_trust(TRUST_VERIFIED_REPORT, f"Your report {label} was real and its fix was verified", code)
            if staff:
                staff.adjust_trust(STAFF_FIX_VERIFIED, f"Students verified your fix on {label}", code)
            issue.recompute_priority()
            return " Fix VERIFIED by affected students."
        return ""

    # ------------------------------------------------------------------ reads
    def visible_issues_for_current_user(self) -> List[Issue]:  # CLI
        u = self.current_user()
        return self.visible_issues(u, "all" if u.role == UserProfile.ROLE_ADMIN else
                                   "queue" if u.role == UserProfile.ROLE_STAFF else "board")

    def visible_issues(self, user: UserProfile, view: str) -> List[Issue]:
        """Issues a user may list in a view (board / mine / queue / all), active ones first, highest score first."""
        qs = Issue.objects.all()
        if view == "all" and user.role == UserProfile.ROLE_ADMIN:
            pass
        elif view == "queue" and user.role == UserProfile.ROLE_STAFF:
            qs = qs.filter(confidential=False, department=user.department)
        elif view == "mine" and user.role == UserProfile.ROLE_STUDENT:
            qs = qs.filter(affected_students=user)
        else:  # board: public issues only
            qs = qs.filter(confidential=False).exclude(status=Issue.STATUS_SPAM)
        active_first = {s: i for i, s in enumerate([Issue.STATUS_REOPENED, Issue.STATUS_OPEN, Issue.STATUS_IN_PROGRESS,
                                                     Issue.STATUS_RESOLVED, Issue.STATUS_NO_ISSUE, Issue.STATUS_VERIFIED,
                                                     Issue.STATUS_CLOSED, Issue.STATUS_SPAM])}
        qs = qs.distinct().select_related("resolved_by").prefetch_related("affected_students", "reports__user", "events")
        return sorted(qs, key=lambda i: (active_first.get(i.status, 9), -i.score, -i.updated_at.timestamp()))

    def trending_board(self) -> List[Tuple[Issue, float]]:
        """Active public issues ranked by score plus recent activity (last 24 h)."""
        since = timezone.now() - timedelta(hours=24)
        out = []
        for i in Issue.objects.filter(confidential=False, status__in=Issue.ACTIVE_STATUSES):
            recent = i.reports.filter(created_at__gte=since).count() + i.events.filter(note="Joined (me too)", created_at__gte=since).count()
            out.append((i, round(i.score + 5 * recent, 1)))
        return sorted(out, key=lambda pair: pair[1], reverse=True)

    def brigading_alerts(self) -> List[str]:
        return [f"[{f.kind}] {f.note}" for f in Flag.objects.order_by("-created_at")[:30]]

    def summary_facts(self) -> Dict:
        """Numbers for the admin summary (confidential cases appear only as a count)."""
        public = list(Issue.objects.filter(confidential=False))
        dept = {d: 0 for d in DEPARTMENTS}
        for i in public:
            if i.status in Issue.ACTIVE_STATUSES and i.department in dept:
                dept[i.department] += 1
        return {
            "open_public_issues": sum(1 for i in public if i.status in Issue.ACTIVE_STATUSES),
            "critical_hazards_open": [f"{i.issue_code}: {i.title} at {i.location}" for i in public
                                      if i.priority == "CRITICAL" and i.status in Issue.ACTIVE_STATUSES],
            "reopened_after_dispute": [f"{i.issue_code}: {i.title} at {i.location}" for i in public if i.status == Issue.STATUS_REOPENED],
            "awaiting_student_votes": [i.issue_code for i in public if i.status in Issue.VOTING_STATUSES],
            "verified_fixes": sum(1 for i in public if i.status == Issue.STATUS_VERIFIED),
            "active_issues_by_department": dept,
            "top_issues_by_score": [f"{i.issue_code}: {i.display_title} at {i.location} ({i.department}, {i.priority})"
                                    for i in sorted(public, key=lambda x: -x.score)[:5] if i.status in Issue.ACTIVE_STATUSES],
            "confidential_cases_open": Issue.objects.filter(confidential=True, status__in=Issue.ACTIVE_STATUSES).count(),
            "flags": [f"{f.kind}: {f.note}" for f in Flag.objects.order_by("-created_at")[:8]],
        }

    def admin_summary(self, use_ai: bool = True) -> str:
        return self.admin_summary_with_source(use_ai)[0]

    def admin_summary_with_source(self, use_ai: bool = True) -> Tuple[str, str]:
        """Admin briefing written by Gemini, or a rule-based summary if AI is unavailable. Returns (text, source)."""
        facts = self.summary_facts()
        if use_ai and settings.GEMINI_API_KEY:
            try:
                return ai.summarize(facts), "gemini"
            except Exception as e:
                ai.STATUS["last_error"] = str(e)[:300]
        busiest = max(facts["active_issues_by_department"].items(), key=lambda kv: kv[1])
        lines = [
            f"- {facts['open_public_issues']} active public issues; {facts['verified_fixes']} fixes verified by students.",
            f"- Safety hazards open: {', '.join(facts['critical_hazards_open']) or 'none'}.",
            f"- Reopened after student disputes: {', '.join(facts['reopened_after_dispute']) or 'none'}.",
            f"- Busiest department: {busiest[0]} ({busiest[1]} active).",
            f"- Confidential cases open: {facts['confidential_cases_open']}. Flags: {len(facts['flags'])}.",
        ]
        return "\n".join(lines), "fallback"
