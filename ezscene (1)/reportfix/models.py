from __future__ import annotations

import math
from datetime import timedelta

from django.db import models
from django.utils import timezone

def now():
    return timezone.now()


DEPARTMENTS = ["EE", "CSE", "IS", "Library", "Cafeteria", "Labs"]
CONFIDENTIAL_QUEUE = "CONFIDENTIAL"

NEW_ACCOUNT_MINUTES = 30
NEW_ACCOUNT_WEIGHT = 0.5

# Base score per category (code decides priority, never the AI).
CATEGORY_BASE = {
    "electrical": 40, "water": 40,
    "wifi": 30, "ac": 30, "projector": 30, "computers": 30, "fan": 25,
    "food": 20, "cleanliness": 20, "furniture": 20,
    "safety": 40, "harassment": 80, "other": 15,
}


class AppSessionState(models.Model):
    """Counters + the CLI's active user (the web app uses a cookie per browser instead)."""

    singleton_id = models.IntegerField(primary_key=True, default=1)
    current_user_code = models.CharField(max_length=32, default="s1")
    issue_counter = models.IntegerField(default=0)
    user_counter = models.IntegerField(default=0)


class UserProfile(models.Model):
    ROLE_STUDENT = "student"
    ROLE_STAFF = "staff"
    ROLE_ADMIN = "admin"
    ROLE_CHOICES = [(ROLE_STUDENT, "Student"), (ROLE_STAFF, "Department Staff"), (ROLE_ADMIN, "Admin")]
    DEPT_CHOICES = [(d, d) for d in DEPARTMENTS]

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    department = models.CharField(max_length=20, choices=DEPT_CHOICES, null=True, blank=True)
    trust_score = models.FloatField(default=50.0)  # 0-100, everyone starts at 50
    created_at = models.DateTimeField(default=now)

    def adjust_trust(self, delta: float, reason: str, issue_code: str = "") -> None:
        """Every rating change is logged with its reason (rule 8).

        Students: trust rating (weights their reports and votes).
        Staff: service rating (falls when a fix they marked resolved is disputed by students).
        """
        if self.role not in (self.ROLE_STUDENT, self.ROLE_STAFF):
            return
        old = self.trust_score
        self.trust_score = round(max(0.0, min(100.0, old + delta)), 1)
        self.save(update_fields=["trust_score"])
        TrustEvent.objects.create(
            user=self,
            delta=round(self.trust_score - old, 1),
            new_score=self.trust_score,
            reason=reason,
            issue_code=issue_code,
        )

    def is_new_account(self) -> bool:
        young = timezone.now() - self.created_at < timedelta(minutes=NEW_ACCOUNT_MINUTES)
        has_history = self.trust_events.exclude(delta=0).exists()
        return young and not has_history

    def weight(self) -> float:
        """How much this student's report or vote counts."""
        base = max(0.2, min(1.5, self.trust_score / 50.0))
        if self.is_new_account():
            return min(base, NEW_ACCOUNT_WEIGHT)
        return round(base, 2)

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class TrustEvent(models.Model):
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="trust_events")
    created_at = models.DateTimeField(default=now)
    delta = models.FloatField()
    new_score = models.FloatField()
    reason = models.CharField(max_length=255)
    issue_code = models.CharField(max_length=32, blank=True, default="")


class Issue(models.Model):
    PRIORITY_CHOICES = [("CRITICAL", "CRITICAL"), ("HIGH", "HIGH"), ("MEDIUM", "MEDIUM"), ("LOW", "LOW")]

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_RESOLVED = "resolved"  # staff says fixed; affected students vote
    STATUS_VERIFIED = "verified"  # crowd confirmed the fix
    STATUS_REOPENED = "reopened"  # crowd disputed the fix
    STATUS_SPAM = "spam"
    STATUS_NO_ISSUE = "no_issue"  # staff checked and found nothing wrong; affected students vote
    STATUS_CLOSED = "closed"      # students agreed there was no issue
    ACTIVE_STATUSES = [STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_REOPENED, STATUS_NO_ISSUE]
    VOTING_STATUSES = [STATUS_RESOLVED, STATUS_NO_ISSUE]

    issue_code = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=200)  # = general_title, kept for older code
    general_title = models.CharField(max_length=120, default="General fix")  # e.g. "AC fix"
    group_key = models.CharField(max_length=80, db_index=True, default="")  # place + problem, e.g. "library:ac"
    report_count = models.IntegerField(default=0)  # distinct students who reported or said "me too"
    resolved_by = models.ForeignKey("UserProfile", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="resolved_issues")
    category = models.CharField(max_length=32, default="other")
    place_id = models.CharField(max_length=40, blank=True, default="")
    location = models.CharField(max_length=120)
    department = models.CharField(max_length=32)  # a department, or CONFIDENTIAL
    priority = models.CharField(max_length=16, choices=PRIORITY_CHOICES, default="LOW")
    score = models.FloatField(default=0.0)
    priority_reason = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=32, default=STATUS_OPEN)
    confidential = models.BooleanField(default=False)
    hazard = models.CharField(max_length=20, blank=True, default="")

    reporters = models.ManyToManyField(UserProfile, related_name="reported_issues", blank=True)
    affected_students = models.ManyToManyField(UserProfile, related_name="affected_issues", blank=True)

    reopen_count = models.IntegerField(default=0)
    round = models.IntegerField(default=0)  # +1 each time staff marks it resolved
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(default=now)

    @property
    def me_too_count(self) -> int:
        return self.affected_students.count()

    @property
    def display_title(self) -> str:
        return f"{self.general_title} (total reports: {self.report_count})"

    def refresh_count(self) -> None:
        self.report_count = self.affected_students.count()
        self.save(update_fields=["report_count"])

    def crowd_score(self) -> float:
        return self.score

    def recompute_priority(self, save: bool = True) -> None:
        """Priority comes from rules: hazard floor, category base, trust-weighted crowd."""
        affected = list(self.affected_students.all())
        weight_sum = sum(u.weight() for u in affected)
        if self.status in (self.STATUS_SPAM, self.STATUS_CLOSED):
            self.score, self.priority = 0.0, "LOW"
            self.priority_reason = "Closed: marked as spam, or students agreed there was no issue."
        elif self.hazard:
            self.score, self.priority = 100.0, "CRITICAL"
            self.priority_reason = f"Safety hazard ({self.hazard}): always CRITICAL, whatever the tone."
        elif self.confidential:
            self.score, self.priority = 80.0, "HIGH"
            self.priority_reason = "Confidential welfare report: handled privately by admin."
        else:
            base = CATEGORY_BASE.get(self.category, 15)
            crowd = 15.0 * math.log(1.0 + weight_sum)
            reopen_bonus = 5.0 * self.reopen_count
            self.score = round(min(base + crowd + reopen_bonus, 95.0), 1)
            if self.score >= 60:
                self.priority = "HIGH"
            elif self.score >= 35:
                self.priority = "MEDIUM"
            else:
                self.priority = "LOW"
            extra = f" + reopen bonus {reopen_bonus:.0f}" if reopen_bonus else ""
            self.priority_reason = (
                f"Base {base} ({self.category}) + crowd {crowd:.1f} from {len(affected)} affected "
                f"student(s), total trust weight {weight_sum:.2f}{extra} = {self.score:.1f} → {self.priority}."
            )
        if save:
            self.save(update_fields=["score", "priority", "priority_reason"])


class ReportEntry(models.Model):
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="reports", null=True, blank=True)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="submitted_reports")
    raw_text = models.TextField()
    clean_text = models.TextField(blank=True, default="")
    triage = models.JSONField(default=dict, blank=True)
    flags = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=16, default="fallback")  # gemini | fallback
    pending = models.BooleanField(default=False)  # waiting for place clarification
    created_at = models.DateTimeField(default=now)


class ResolutionVote(models.Model):
    CHOICE_CONFIRM = "confirm"
    CHOICE_DISPUTE = "dispute"

    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="votes")
    voter = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="resolution_votes")
    choice = models.CharField(max_length=16)
    weight = models.FloatField(default=1.0)
    round = models.IntegerField(default=1)
    discounted = models.BooleanField(default=False)  # brigading: counts 0
    created_at = models.DateTimeField(default=now)

    class Meta:
        unique_together = ("issue", "voter", "round")


class IssueEvent(models.Model):
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=32)
    by = models.CharField(max_length=120)
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=now)


class Flag(models.Model):
    KIND_CHOICES = [("injection", "injection"), ("brigading", "brigading"), ("spam", "spam")]

    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, null=True, blank=True, related_name="flags")
    user_codes = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=now)


class PortalAccount(models.Model):
    """Login credentials, and which portal each credential may sign in to.

    One row per user. `portal` must equal the user's role: a student credential can never
    open the staff or admin portal, and a staff credential only opens its own department queue.
    Only a salted hash of the password is stored.
    """

    PORTAL_STUDENT = "student"
    PORTAL_STAFF = "staff"
    PORTAL_ADMIN = "admin"
    PORTAL_CHOICES = [(PORTAL_STUDENT, "Student portal"), (PORTAL_STAFF, "Department staff portal"),
                      (PORTAL_ADMIN, "Admin portal")]

    user = models.OneToOneField(UserProfile, on_delete=models.CASCADE, related_name="account")
    username = models.CharField(max_length=64, unique=True)  # CMS ID, Staff ID, or admin username
    email = models.CharField(max_length=120, unique=True, null=True, blank=True)  # can also sign in with email
    password_hash = models.CharField(max_length=256)
    portal = models.CharField(max_length=16, choices=PORTAL_CHOICES)
    department = models.CharField(max_length=20, null=True, blank=True)  # staff portal scope
    is_active = models.BooleanField(default=True)
    failed_attempts = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=now)

    def set_password(self, raw: str) -> None:
        from django.contrib.auth.hashers import make_password
        self.password_hash = make_password(raw)

    def check_password(self, raw: str) -> bool:
        from django.contrib.auth.hashers import check_password
        return check_password(raw, self.password_hash)

    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())


class LoginAttempt(models.Model):
    """Audit log of every sign-in attempt (success or failure)."""

    username = models.CharField(max_length=64)
    portal = models.CharField(max_length=16)
    success = models.BooleanField(default=False)
    reason = models.CharField(max_length=120)
    ip = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(default=now)



class Department(models.Model):
    """The six departments that receive complaints."""

    code = models.CharField(primary_key=True, max_length=20)  # EE, CSE, IS, Library, Cafeteria, Labs
    name = models.CharField(max_length=120)
    office = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=11)
    head = models.CharField(max_length=120)

    def __str__(self) -> str:
        return self.code


class Location(models.Model):
    """Campus places; each belongs to one department (routing)."""

    id = models.CharField(primary_key=True, max_length=40)
    name = models.CharField(max_length=120)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="locations")
    kind = models.CharField(max_length=20, default="place")  # lab, classroom, library, cafeteria, hall, office
    aliases = models.JSONField(default=list, blank=True)


class Student(models.Model):
    """Student record. CMS ID (5 digits) is the primary key and the login username."""

    cms_id = models.CharField(primary_key=True, max_length=5)
    profile = models.OneToOneField(UserProfile, on_delete=models.CASCADE, related_name="student")
    name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)      # name + CMS ID + @mcs.nust.pk
    phone = models.CharField(max_length=11)     # 11 digits, starts with 55
    program = models.CharField(max_length=20)   # BEE, BESE, BSIS
    semester = models.IntegerField(default=1)
    section = models.CharField(max_length=2, default="A")
    created_at = models.DateTimeField(default=now)


class Staff(models.Model):
    """Department staff. Staff ID is the primary key and the login username.

    `department` is assigned by the admin; the staff member's portal opens that department's queue.
    """

    staff_id = models.CharField(primary_key=True, max_length=10)
    profile = models.OneToOneField(UserProfile, on_delete=models.CASCADE, related_name="staff")
    name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=11)
    designation = models.CharField(max_length=120)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff")
    joined_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)   # admin "remove" = deactivate (history is kept)
    created_at = models.DateTimeField(default=now)
