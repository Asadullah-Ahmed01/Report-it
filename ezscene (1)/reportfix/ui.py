"""EzScene — server-rendered Django pages (desktop UI).

Every page reads the signed-in user from the server-side session and calls the same rules engine as the JSON
API, so permissions and rules are identical in both. Forms post back with a CSRF token and redirect (POST →
redirect → GET), so refreshing a page never re-submits a report.

URL map (see urls.py):
    /                       -> sends you to your portal (or /login/)
    /login/  /logout/       -> sign-in (portal detected from the credentials) / sign-out
    /judge/...              -> judge-mode tools: switch user, new student, reset (only when JUDGE_MODE=1)
    /student/               -> report form, my issues, places to pick for unclear reports
    /board/                 -> public campus board + trending
    /issues/<code>/         -> one issue: reports, history, vote / me-too / staff actions
    /staff/<dept>/          -> the signed-in staff member's department queue
    /admin/ ...             -> admin: complaints by state, staff, students, reports
    /people/<code>/         -> why someone has their rating
"""
from __future__ import annotations

from functools import wraps
from typing import Callable, Optional

from django.conf import settings
from django.db.models import Count, Prefetch, Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from reportfix import auth
from reportfix.api import STATE_GROUPS, can_view
from reportfix.engine import DjangoReportFixEngine
from reportfix.models import (Department, Flag, Issue, ReportEntry, ResolutionVote, Staff, Student, TrustEvent,
                              UserProfile)
from reportfix.places import PLACE_BY_ID, PLACES

engine = DjangoReportFixEngine()


# ------------------------------------------------------------------ helpers
def flash(request: HttpRequest, text: str, kind: str = "info") -> None:
    """Queue a one-time message for the next page (stored in the session)."""
    request.session.setdefault("flash", []).append({"text": text, "kind": kind})
    request.session.modified = True


def current_user(request: HttpRequest) -> Optional[UserProfile]:
    """The signed-in user from the server-side session, or None (then the page sends you to sign in).

    The web UI never signs anyone in automatically: every visit starts on the sign-in page. In judge mode the
    sign-in page also offers the one-click user switcher, so judges still need no password.
    """
    engine.ensure_initialized()
    return auth.session_user(request)


def page(*roles: str) -> Callable:
    """Decorator: require a signed-in user (optionally with one of `roles`) and pass it to the view."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = current_user(request)
            if user is None:
                return redirect("ui-login")
            if roles and user.role not in roles:
                return redirect("ui-home")
            return view(request, user, *args, **kwargs)
        return wrapper
    return deco


def render_page(request: HttpRequest, user: Optional[UserProfile], template: str, **ctx) -> HttpResponse:
    """Render with the shared layout context (nav, judge bar, flash messages)."""
    nav = {"student.html": "student", "board.html": "board", "staff.html": "staff", "admin.html": "admin",
           "admin_staff.html": "staff", "admin_students.html": "students", "admin_reports.html": "reports",
           "person.html": "me"}.get(template, "")
    ctx.update({
        "nav": ctx.get("nav", nav),
        "me": user,
        "judge_mode": settings.JUDGE_MODE,
        "flashes": request.session.pop("flash", []),
        "switch_users": list(UserProfile.objects.exclude(staff__is_active=False).order_by("role", "name"))
        if settings.JUDGE_MODE else [],
    })
    return render(request, f"reportfix/{template}", ctx)


def landing_url(user: UserProfile) -> str:
    """Portal home for a user; staff land on their department queue."""
    if user.role == UserProfile.ROLE_STAFF:
        return f"/staff/{user.department.lower()}/" if user.department else "/staff/unassigned/"
    return {"student": "/student/", "admin": "/admin/"}.get(user.role, "/login/")


def with_issue_prefetch(qs):
    """One query each for affected students, reports and their authors (avoids N+1 on lists)."""
    return qs.select_related("resolved_by").prefetch_related(
        "affected_students", Prefetch("reports", queryset=ReportEntry.objects.select_related("user").order_by("created_at")))


def decorate(issue: Issue, viewer: UserProfile) -> Issue:
    """Attach per-viewer fields the templates need (affected?, my vote, can manage, vote tallies)."""
    affected_ids = {u.id for u in issue.affected_students.all()}
    issue.is_affected = viewer.id in affected_ids
    issue.can_manage = engine.can_manage(viewer, issue)
    issue.voting_open = issue.status in Issue.VOTING_STATUSES
    issue.my_vote = None
    if issue.is_affected and issue.round:
        vote = ResolutionVote.objects.filter(issue=issue, voter=viewer, round=issue.round).first()
        issue.my_vote = vote.choice if vote else None
    if issue.voting_open:
        issue.confirm_w, issue.dispute_w = engine.tallies(issue)
        issue.need_w = engine.vote_threshold(issue)
    return issue


def back(request: HttpRequest, fallback: str = "/") -> HttpResponse:
    """Return to the page the form was posted from (only same-site paths)."""
    nxt = request.POST.get("next") or request.META.get("HTTP_REFERER") or fallback
    return redirect(nxt if nxt.startswith("/") or "://" + request.get_host() in nxt else fallback)


# ------------------------------------------------------------------ entry, login, judge tools
@ensure_csrf_cookie
def home(request: HttpRequest) -> HttpResponse:
    """`/`: the landing page is always the sign-in page."""
    return redirect("ui-login")


@ensure_csrf_cookie
def login_view(request: HttpRequest) -> HttpResponse:
    """Sign-in with CMS ID / Staff ID / email. The portal is detected from the PortalAccount row."""
    engine.ensure_initialized()
    error = ""
    if request.method == "POST":
        user, message, status = auth.login(request, "", request.POST.get("username", ""), request.POST.get("password", ""))
        if user:
            portal, path = auth.landing(user)
            flash(request, message if portal != "staff" else f"Welcome {user.name}: {user.department or 'no'} department queue.", "ok")
            return redirect(path)
        error = message
    signed_in = auth.session_user(request)
    return render_page(request, signed_in, "login.html", error=error, username=request.POST.get("username", ""),
                       signed_in=signed_in, continue_url=landing_url(signed_in) if signed_in else "")


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    request.session.flush()
    return redirect("ui-login")


@require_POST
def judge_switch(request: HttpRequest) -> HttpResponse:
    """Judge mode: become any user in one click (no password), as the hackathon rules require."""
    if not settings.JUDGE_MODE:
        return HttpResponseForbidden("Judge mode is off")
    user = engine.get_user(request.POST.get("code", ""))
    if not user or (user.role == "staff" and hasattr(user, "staff") and not user.staff.is_active):
        flash(request, "Unknown user.", "error")
        return redirect("ui-home")
    auth.start_session(request, user, via="judge")
    return redirect(landing_url(user))


@require_POST
def judge_new_student(request: HttpRequest) -> HttpResponse:
    if not settings.JUDGE_MODE:
        return HttpResponseForbidden("Judge mode is off")
    student = engine.create_student(request.POST.get("name", ""))
    auth.start_session(request, student, via="judge")
    flash(request, f"New student {student.name} (CMS {student.code}) created. Password, shown once: {student.new_password}", "ok")
    return redirect("ui-student")


@require_POST
def judge_reset(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    if not settings.JUDGE_MODE and (not user or user.role != "admin"):
        return HttpResponseForbidden("Only admin can reset")
    engine.reset_all_data()
    request.session.flush()
    flash(request, "All data reset to the demo seed.", "ok")
    return redirect("ui-home")


# ------------------------------------------------------------------ student
@page("student")
def student_home(request: HttpRequest, me: UserProfile) -> HttpResponse:
    """Report form, the result of the last report, my issues (reported or joined) and unclear reports."""
    mine = with_issue_prefetch(Issue.objects.filter(affected_students=me)).order_by("-updated_at")
    issues = [decorate(i, me) for i in mine]
    pending = me.submitted_reports.filter(pending=True).order_by("-created_at")
    last = request.session.pop("last_result", None)
    return render_page(request, me, "student.html", issues=issues, pending=pending, last=last,
                       classroom_places=[p for p in PLACES if p.get("kind") == "classroom"], places=PLACES,
                       stats={"open": sum(i.status in Issue.ACTIVE_STATUSES for i in issues),
                              "done": sum(i.status in STATE_GROUPS["completed"] for i in issues), "total": len(issues)})


@require_POST
@page("student")
def report_submit(request: HttpRequest, me: UserProfile) -> HttpResponse:
    """Run a report through triage + rules; keep the outcome for the next page."""
    res = engine.submit_report(me.code, request.POST.get("text", ""))
    issue = res.get("issue")
    request.session["last_result"] = {
        "outcome": res.get("outcome"), "message": res.get("message"),
        "issue": issue.issue_code if issue else None, "injection": bool(res.get("injection_ignored")),
        "report_id": res["report"].id if res.get("report") is not None else None,
        "suggestions": [p["id"] for p in res.get("suggestions") or []],
    }
    return redirect("ui-student")


@require_POST
@page("student")
def report_clarify(request: HttpRequest, me: UserProfile, report_id: int) -> HttpResponse:
    res = engine.clarify_report(me.code, report_id, request.POST.get("place", ""))
    flash(request, res.get("message", ""), "ok" if res.get("ok") else "error")
    return redirect("ui-student")


@page("student", "admin")
def board(request: HttpRequest, me: UserProfile) -> HttpResponse:
    """Public campus board (never confidential) + trending."""
    qs = with_issue_prefetch(Issue.objects.filter(confidential=False, status__in=Issue.ACTIVE_STATUSES))
    dept = request.GET.get("dept", "")
    if dept:
        qs = qs.filter(department=dept)
    issues = sorted((decorate(i, me) for i in qs), key=lambda i: -i.score)
    trending = [i for i, _ in engine.trending_board()[:5]]
    open_by_dept = dict(Issue.objects.filter(confidential=False, status__in=Issue.ACTIVE_STATUSES)
                        .values_list("department").annotate(n=Count("id")))
    tiles = [{"code": d.code, "name": d.name, "open": open_by_dept.get(d.code, 0)} for d in Department.objects.all()]
    return render_page(request, me, "board.html", issues=issues, trending=trending, dept=dept, dept_tiles=tiles)


# ------------------------------------------------------------------ issue page + actions
@page()
def issue_page(request: HttpRequest, me: UserProfile, code: str) -> HttpResponse:
    issue = get_object_or_404(with_issue_prefetch(Issue.objects.all()), issue_code__iexact=code)
    if not can_view(me, issue):
        return HttpResponseForbidden("You are not allowed to see this issue.")
    decorate(issue, me)
    reports = [{"text": r.raw_text, "at": r.created_at, "source": r.source, "flags": r.flags,
                "who": ("You" if r.user_id == me.id else "Hidden") if issue.confidential else r.user.name}
               for r in issue.reports.all()]
    return render_page(request, me, "issue.html", issue=issue, reports=reports,
                       history=issue.events.order_by("created_at"))


@require_POST
@page("student")
def issue_join(request: HttpRequest, me: UserProfile, code: str) -> HttpResponse:
    ok, msg = engine.join_me_too(me.code, code)
    flash(request, msg, "ok" if ok else "error")
    return back(request)


@require_POST
@page("student")
def issue_vote(request: HttpRequest, me: UserProfile, code: str) -> HttpResponse:
    ok, msg = engine.vote_resolution(me.code, code, request.POST.get("value", ""))
    flash(request, msg, "ok" if ok else "error")
    return back(request)


@require_POST
@page("staff", "admin")
def issue_status(request: HttpRequest, me: UserProfile, code: str) -> HttpResponse:
    ok, msg = engine.staff_update_status(me.code, code, request.POST.get("status", ""), request.POST.get("note", "")[:200])
    flash(request, msg, "ok" if ok else "error")
    return back(request)


# ------------------------------------------------------------------ staff
@page("staff")
def staff_queue(request: HttpRequest, me: UserProfile, dept: str = "") -> HttpResponse:
    """Department queue. The department always comes from the database (admin assignment), never the URL."""
    if (me.department or "unassigned").lower() != dept.lower():
        return redirect(landing_url(me))
    qs = with_issue_prefetch(Issue.objects.filter(confidential=False, department=me.department)) if me.department else Issue.objects.none()
    tab = request.GET.get("tab", "todo")
    groups = {"todo": [Issue.STATUS_OPEN, Issue.STATUS_REOPENED, Issue.STATUS_IN_PROGRESS],
              "waiting": Issue.VOTING_STATUSES, "done": STATE_GROUPS["completed"]}
    issues = sorted((decorate(i, me) for i in qs.filter(status__in=groups.get(tab, groups["todo"]))),
                    key=lambda i: (-i.score, i.created_at))
    counts = {k: qs.filter(status__in=v).count() for k, v in groups.items()}
    return render_page(request, me, "staff.html", issues=issues, tab=tab, counts=counts,
                       department=Department.objects.filter(code=me.department).first())


# ------------------------------------------------------------------ admin
@page("admin")
def admin_home(request: HttpRequest, me: UserProfile) -> HttpResponse:
    """Complaints by state (pending / active / completed), headline numbers and alerts."""
    state = request.GET.get("state", "pending")
    statuses = STATE_GROUPS.get(state)
    qs = with_issue_prefetch(Issue.objects.filter(status__in=statuses) if statuses else Issue.objects.all())
    issues = [decorate(i, me) for i in qs.order_by("-updated_at")]
    counts = {k: Issue.objects.filter(status__in=v).count() for k, v in STATE_GROUPS.items()}
    by_dept = (Issue.objects.filter(status__in=Issue.ACTIVE_STATUSES).values("department").annotate(n=Count("id"))
               .order_by("-n"))
    return render_page(request, me, "admin.html", issues=issues, state=state, counts=counts, by_dept=by_dept,
                       awaiting=ReportEntry.objects.filter(pending=True).select_related("user"),
                       flags=Flag.objects.order_by("-created_at")[:8],
                       critical=Issue.objects.filter(priority="CRITICAL", status__in=Issue.ACTIVE_STATUSES).count())


@page("admin")
def admin_staff_page(request: HttpRequest, me: UserProfile) -> HttpResponse:
    """List, add, assign and remove staff."""
    if request.method == "POST":
        action = request.POST.get("action")
        sid = request.POST.get("staff_id", "")
        try:
            if action == "add":
                staff, raw = engine.add_staff(request.POST.get("name", ""), request.POST.get("department") or None,
                                              request.POST.get("designation", ""), request.POST.get("email", ""),
                                              request.POST.get("phone", ""))
                flash(request, f"Added {staff.name}. Sign-in: {staff.staff_id} (or {staff.email}) / {raw} (shown once).", "ok")
            elif action == "assign":
                staff = engine.assign_staff(sid, request.POST.get("department") or None)
                flash(request, f"{staff.name} now handles {staff.department_id or 'no department'}.", "ok")
            elif action in ("remove", "restore"):
                staff = engine.remove_staff(sid, restore=action == "restore")
                flash(request, f"{staff.name} {'restored' if action == 'restore' else 'removed (can no longer sign in)'}.", "ok")
        except (ValueError, LookupError) as e:
            flash(request, str(e), "error")
        return redirect("ui-admin-staff")
    staff = (Staff.objects.select_related("profile", "department")
             .annotate(handled=Count("profile__resolved_issues")).order_by("-is_active", "department_id", "name"))
    return render_page(request, me, "admin_staff.html", staff=staff, departments=Department.objects.all())


@page("admin")
def admin_students_page(request: HttpRequest, me: UserProfile) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    qs = Student.objects.select_related("profile").annotate(n_reports=Count("profile__submitted_reports"))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(cms_id__icontains=q) | Q(email__icontains=q))
    return render_page(request, me, "admin_students.html", students=qs.order_by("name"), q=q)


@page("admin")
def admin_reports_page(request: HttpRequest, me: UserProfile) -> HttpResponse:
    """Weekly / monthly report downloads (CSV is produced by api.admin_reports)."""
    return render_page(request, me, "admin_reports.html")


# ------------------------------------------------------------------ ratings
@page()
def person_page(request: HttpRequest, me: UserProfile, code: str) -> HttpResponse:
    """Why someone has their rating: every change with its reason (rule 8)."""
    person = get_object_or_404(UserProfile.objects.select_related("student", "staff"), code__iexact=code)
    events = TrustEvent.objects.filter(user=person).order_by("-created_at", "-id")
    return render_page(request, me, "person.html", person=person, events=events,
                       weight=person.weight() if person.role == "student" else None)
