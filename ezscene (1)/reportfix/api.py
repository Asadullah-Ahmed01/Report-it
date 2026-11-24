"""JSON API. The current user comes from the rf_uid cookie; every permission is checked here."""
from __future__ import annotations

import json
from typing import Dict, Optional

from django.conf import settings
from django.http import HttpRequest, JsonResponse

from reportfix import ai, auth
from reportfix.engine import DjangoReportFixEngine
import csv
import io
from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone

from reportfix.models import (Department, Flag, Issue, LoginAttempt, PortalAccount, ReportEntry, ResolutionVote, Staff,
                              Student, UserProfile)
from reportfix.places import PLACE_BY_ID, PLACES

engine = DjangoReportFixEngine()


# ------------------------------------------------------------------ helpers
def _body(request: HttpRequest) -> Dict:
    try:
        return json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return {}


def _err(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


class LoginRequired(Exception):
    pass


def _current(request: HttpRequest) -> UserProfile:
    """The signed-in user from the server-side session. Never trusts a client-sent id."""
    engine.ensure_initialized()
    user = auth.session_user(request)
    if user is None and settings.JUDGE_MODE:
        user = UserProfile.objects.filter(role="student").order_by("id").first()  # judge mode: start as a demo student
        if user:
            auth.start_session(request, user, via="judge")
    if user is None:
        raise LoginRequired()
    return user


def login_required_json(view):
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except LoginRequired:
            return _err("Please sign in.", 401)
    wrapper.__name__ = view.__name__
    return wrapper


def _with_cookie(resp: JsonResponse, user: UserProfile) -> JsonResponse:
    return resp  # kept for call sites; identity now lives in the server-side session


def _method(request: HttpRequest, *allowed: str) -> Optional[JsonResponse]:
    if request.method not in allowed:
        return _err(f"Method {request.method} not allowed", 405)
    return None


def user_json(u: UserProfile) -> Dict:
    extra: Dict = {}
    st = getattr(u, "student", None) if u.role == "student" else None
    sf = getattr(u, "staff", None) if u.role == "staff" else None
    if st:
        extra = {"cmsId": st.cms_id, "email": st.email, "phone": st.phone, "program": st.program,
                 "semester": st.semester, "section": st.section}
    elif sf:
        extra = {"staffId": sf.staff_id, "email": sf.email, "phone": sf.phone, "designation": sf.designation,
                 "active": sf.is_active, "joinedOn": sf.joined_on.isoformat() if sf.joined_on else None}
    return {**extra, "code": u.code, "name": u.name, "role": u.role, "department": u.department,
            "trust": u.trust_score if u.role == "student" else None,
            "rating": u.trust_score if u.role in ("student", "staff") else None,
            "portal": u.role, "weight": u.weight() if u.role == "student" else None,
            "isNew": u.is_new_account() if u.role == "student" else False}


def can_view(user: UserProfile, issue: Issue) -> bool:
    if user.role == UserProfile.ROLE_ADMIN:
        return True
    if user.role == UserProfile.ROLE_STAFF:
        return not issue.confidential and issue.department == user.department
    return not issue.confidential or issue.affected_students.filter(pk=user.pk).exists()


def issue_json(issue: Issue, viewer: UserProfile) -> Dict:
    affected_ids = {u.id for u in issue.affected_students.all()}  # uses the prefetch when present
    my_vote = None
    if viewer.id in affected_ids:
        v = ResolutionVote.objects.filter(issue=issue, voter=viewer, round=issue.round).first()
        my_vote = v.choice if v else None
    wc, wd = engine.tallies(issue)
    reports = []
    for r in sorted(issue.reports.all(), key=lambda r: r.created_at):
        if issue.confidential:
            reporter = "You" if r.user_id == viewer.id else "Hidden"
        else:
            reporter = r.user.name
        reports.append({"text": r.raw_text, "at": r.created_at.isoformat(), "reporter": reporter,
                        "source": r.source, "flags": r.flags})
    return {
        "code": issue.issue_code, "title": issue.general_title, "generalTitle": issue.general_title,
        "totalReports": issue.report_count, "displayTitle": issue.display_title, "groupKey": issue.group_key,
        "resolvedBy": issue.resolved_by.name if issue.resolved_by else None,
        "votingOpen": issue.status in Issue.VOTING_STATUSES, "category": issue.category,
        "placeId": issue.place_id, "place": issue.location, "department": issue.department,
        "priority": issue.priority, "score": issue.score, "priorityReason": issue.priority_reason,
        "reason": issue.reason, "status": issue.status, "confidential": issue.confidential,
        "hazard": issue.hazard or None, "round": issue.round, "reopenCount": issue.reopen_count,
        "meTooCount": len(affected_ids), "isAffected": viewer.id in affected_ids, "myVote": my_vote,
        "confirmWeight": wc, "disputeWeight": wd, "votesNeeded": engine.vote_threshold(issue),
        "canManage": engine.can_manage(viewer, issue),
        "reports": reports,
        "history": [{"at": e.created_at.isoformat(), "status": e.status, "by": e.by, "note": e.note}
                    for e in sorted(issue.events.all(), key=lambda e: e.created_at)],
        "createdAt": issue.created_at.isoformat(), "updatedAt": issue.updated_at.isoformat(),
    }


def _report_result(res: Dict, viewer: UserProfile) -> JsonResponse:
    payload = {
        "ok": res.get("ok", False), "outcome": res.get("outcome"), "message": res.get("message"),
        "injectionIgnored": bool(res.get("injection_ignored")),
    }
    if res.get("issue") is not None:
        payload["issue"] = issue_json(res["issue"], viewer)
    if res.get("report") is not None:
        r = res["report"]
        payload["report"] = {"id": r.id, "text": r.raw_text, "flags": r.flags, "source": r.source, "pending": r.pending}
    if res.get("suggestions") is not None:
        payload["suggestions"] = res["suggestions"]
    status = 200 if res.get("outcome") in ("created", "merged", "needs_clarification") else res.get("status", 400)
    return JsonResponse(payload, status=status)


# ------------------------------------------------------------------ endpoints
def health(request: HttpRequest) -> JsonResponse:
    out = {"status": "ok", "judgeMode": settings.JUDGE_MODE, "ai": {"configured": bool(settings.GEMINI_API_KEY), "model": settings.GEMINI_MODEL, **ai.STATUS},
           "database": settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]}
    if request.GET.get("test") == "1":
        tri, _, _ = engine.triage("Lab 3 ka wifi subah se nahi chal raha", use_ai=True)
        out["test"] = tri
        out["ai"] = {"configured": bool(settings.GEMINI_API_KEY), "model": settings.GEMINI_MODEL, **ai.STATUS}
    return JsonResponse(out)


@login_required_json
def session(request: HttpRequest) -> JsonResponse:
    bad = _method(request, "GET", "POST")
    if bad:
        return bad
    if request.method == "POST":
        if not settings.JUDGE_MODE:
            return _err("One-click switching is off (JUDGE_MODE=0). Please sign in.", 403)
        engine.ensure_initialized()
        user = engine.get_user(str(_body(request).get("userId", "")))
        if not user:
            return _err("Unknown user", 404)
        if user.role == "staff" and not getattr(user, "staff", None) is None and not user.staff.is_active:
            return _err("This staff member was removed by the admin", 403)
        auth.start_session(request, user, via="judge")
        return JsonResponse({"user": user_json(user), "judgeMode": True})
    user = _current(request)
    return JsonResponse({"user": user_json(user), "judgeMode": settings.JUDGE_MODE,
                         "via": request.session.get("via")})


@login_required_json
def users(request: HttpRequest) -> JsonResponse:
    bad = _method(request, "GET", "POST")
    if bad:
        return bad
    engine.ensure_initialized()
    actor = _current(request)
    if request.method == "POST":
        if not settings.JUDGE_MODE and actor.role != UserProfile.ROLE_ADMIN:
            return _err("Only admin can create accounts when judge mode is off", 403)
        student = engine.create_student(str(_body(request).get("name", "")))
        if settings.JUDGE_MODE:
            auth.start_session(request, student, via="judge")
        return JsonResponse({"user": user_json(student),
                             "credentials": {"portal": "student", "username": student.code, "password": student.new_password}})
    if not settings.JUDGE_MODE and actor.role != UserProfile.ROLE_ADMIN:
        return JsonResponse({"users": [user_json(u) for u in UserProfile.objects.filter(role="student")]})
    role_order = {"student": 0, "staff": 1, "admin": 2}
    everyone = sorted(UserProfile.objects.select_related("student", "staff").exclude(staff__is_active=False),
                      key=lambda u: (role_order.get(u.role, 9), u.id))
    return JsonResponse({"users": [user_json(u) for u in everyone]})


@login_required_json
def user_trust(request: HttpRequest, code: str) -> JsonResponse:
    _current(request)
    target = engine.get_user(code)
    if not target:
        return _err("Unknown user", 404)
    log = [{"at": e.created_at.isoformat(), "delta": e.delta, "score": e.new_score, "reason": e.reason,
            "issue": e.issue_code} for e in target.trust_events.order_by("-created_at", "-id")]
    return JsonResponse({"user": user_json(target), "trust": target.trust_score, "weight": target.weight(),
                         "weightRule": "weight = trust / 50, clamped to 0.2–1.5; brand-new accounts (under 30 min, no history) max 0.5",
                         "log": log})


@login_required_json
def reset(request: HttpRequest) -> JsonResponse:
    bad = _method(request, "POST")
    if bad:
        return bad
    if not settings.JUDGE_MODE and _current(request).role != UserProfile.ROLE_ADMIN:
        return _err("Only admin can reset data when judge mode is off", 403)
    engine.reset_all_data()
    request.session.flush()
    user = UserProfile.objects.filter(role="student").order_by("id").first()
    if settings.JUDGE_MODE:
        auth.start_session(request, user, via="judge")
    return JsonResponse({"ok": True, "user": user_json(user) if settings.JUDGE_MODE else None})


def places(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"places": PLACES})


@login_required_json
def reports(request: HttpRequest) -> JsonResponse:
    bad = _method(request, "POST")
    if bad:
        return bad
    user = _current(request)
    if user.role != UserProfile.ROLE_STUDENT:
        return _err("Only students can submit reports", 403)
    res = engine.submit_report(user.code, str(_body(request).get("text", "")))
    return _report_result(res, user)


@login_required_json
def clarify(request: HttpRequest, report_id: int) -> JsonResponse:
    bad = _method(request, "POST")
    if bad:
        return bad
    user = _current(request)
    place_id = str(_body(request).get("placeId", ""))
    if place_id not in PLACE_BY_ID:
        return _err("Unknown place", 400)
    res = engine.clarify_report(user.code, report_id, place_id)
    return _report_result(res, user)


@login_required_json
def issues(request: HttpRequest) -> JsonResponse:
    user = _current(request)
    view = request.GET.get("view", "board")
    allowed = {"student": {"board", "mine"}, "staff": {"queue"}, "admin": {"all", "board"}}
    if view not in allowed.get(user.role, set()):
        return _err(f"View '{view}' is not available for role {user.role}", 403)
    items = [issue_json(i, user) for i in engine.visible_issues(user, view)]
    out = {"view": view, "issues": items}
    if view == "mine":
        out["pending"] = [
            {"id": r.id, "text": r.raw_text, "at": r.created_at.isoformat(),
             "mention": (r.triage or {}).get("place_mention"),
             "suggestions": [p for p in PLACES]}
            for r in user.submitted_reports.filter(pending=True).order_by("-created_at")
        ]
    return JsonResponse(out)


def _get_issue(request: HttpRequest, code: str):
    user = _current(request)
    issue = Issue.objects.filter(issue_code__iexact=code).first()
    if not issue:
        return user, None, _err("Issue not found", 404)
    if not can_view(user, issue):
        return user, None, _err("You are not allowed to see this issue", 403)
    return user, issue, None


@login_required_json
def issue_detail(request: HttpRequest, code: str) -> JsonResponse:
    user, issue, bad = _get_issue(request, code)
    if bad:
        return bad
    return JsonResponse({"issue": issue_json(issue, user)})


@login_required_json
def join(request: HttpRequest, code: str) -> JsonResponse:
    bad = _method(request, "POST")
    if bad:
        return bad
    user, issue, bad = _get_issue(request, code)
    if bad:
        return bad
    ok, msg = engine.join_me_too(user.code, issue.issue_code)
    issue.refresh_from_db()
    return JsonResponse({"ok": ok, "message": msg, "issue": issue_json(issue, user)}, status=200 if ok else 400)


@login_required_json
def status(request: HttpRequest, code: str) -> JsonResponse:
    bad = _method(request, "POST")
    if bad:
        return bad
    user, issue, bad = _get_issue(request, code)
    if bad:
        return bad
    if not engine.can_manage(user, issue):
        return _err("Only staff of this department (or admin) can change its status", 403)
    body = _body(request)
    ok, msg = engine.staff_update_status(user.code, issue.issue_code, str(body.get("status", "")), str(body.get("note", ""))[:200])
    issue.refresh_from_db()
    return JsonResponse({"ok": ok, "message": msg, "issue": issue_json(issue, user)}, status=200 if ok else 400)


@login_required_json
def vote(request: HttpRequest, code: str) -> JsonResponse:
    bad = _method(request, "POST")
    if bad:
        return bad
    user, issue, bad = _get_issue(request, code)
    if bad:
        return bad
    ok, msg = engine.vote_resolution(user.code, issue.issue_code, str(_body(request).get("value", "")))
    issue.refresh_from_db()
    return JsonResponse({"ok": ok, "message": msg, "issue": issue_json(issue, user)}, status=200 if ok else 403)


@login_required_json
def trending(request: HttpRequest) -> JsonResponse:
    user = _current(request)
    top = engine.trending_board()[:5]
    return JsonResponse({"issues": [dict(issue_json(i, user), trendScore=s) for i, s in top]})


@login_required_json
def admin_summary(request: HttpRequest) -> JsonResponse:
    user = _current(request)
    if user.role != UserProfile.ROLE_ADMIN:
        return _err("Admin only", 403)
    text, source = engine.admin_summary_with_source(use_ai=True)
    return JsonResponse({"summary": text, "source": source, "aiError": ai.STATUS.get("last_error") if source == "fallback" else ""})


@login_required_json
def admin_flags(request: HttpRequest) -> JsonResponse:
    user = _current(request)
    if user.role != UserProfile.ROLE_ADMIN:
        return _err("Admin only", 403)
    flags = [{"kind": f.kind, "note": f.note, "issue": f.issue.issue_code if f.issue else None,
              "users": f.user_codes, "at": f.created_at.isoformat()} for f in Flag.objects.order_by("-created_at")[:50]]
    return JsonResponse({"flags": flags})


# ------------------------------------------------------------------ authentication
def auth_login(request: HttpRequest) -> JsonResponse:
    bad = _method(request, "POST")
    if bad:
        return bad
    engine.ensure_initialized()
    body = _body(request)
    user, message, status = auth.login(request, str(body.get("portal", "")), str(body.get("username", "")),
                                       str(body.get("password", "")))
    if user is None:
        return _err(message, status)
    portal, path = auth.landing(user)
    where = f"the {user.department} queue" if portal == "staff" and user.department else f"the {portal} portal"
    if portal == "staff" and not user.department:
        where = "the staff portal (no department assigned yet; ask the admin)"
    return JsonResponse({"user": user_json(user), "portal": portal, "department": user.department, "redirect": path,
                         "message": f"Signed in to {where}."})


def auth_logout(request: HttpRequest) -> JsonResponse:
    bad = _method(request, "POST")
    if bad:
        return bad
    request.session.flush()
    return JsonResponse({"ok": True})


def auth_me(request: HttpRequest) -> JsonResponse:
    engine.ensure_initialized()
    user = auth.session_user(request)
    return JsonResponse({"user": user_json(user) if user else None, "judgeMode": settings.JUDGE_MODE,
                         "via": request.session.get("via") if user else None,
                         "portals": [{"id": k, "label": v} for k, v in PortalAccount.PORTAL_CHOICES]})


@login_required_json
def admin_accounts(request: HttpRequest) -> JsonResponse:
    user = _current(request)
    if user.role != UserProfile.ROLE_ADMIN:
        return _err("Admin only", 403)
    accounts = [{"username": a.username, "name": a.user.name, "portal": a.portal, "department": a.department,
                 "active": a.is_active, "failedAttempts": a.failed_attempts, "locked": a.is_locked(),
                 "lastLogin": a.last_login_at.isoformat() if a.last_login_at else None}
                for a in PortalAccount.objects.select_related("user").order_by("portal", "username")]
    attempts = [{"username": a.username, "portal": a.portal, "success": a.success, "reason": a.reason,
                 "ip": a.ip, "at": a.created_at.isoformat()} for a in LoginAttempt.objects.order_by("-created_at")[:30]]
    return JsonResponse({"accounts": accounts, "attempts": attempts})


# ------------------------------------------------------------------ admin: complaints, reports, staff
STATE_GROUPS = {
    "pending": [Issue.STATUS_OPEN, Issue.STATUS_REOPENED],                     # waiting for staff to act
    "active": [Issue.STATUS_IN_PROGRESS, Issue.STATUS_RESOLVED, Issue.STATUS_NO_ISSUE],  # being worked / awaiting student confirmation
    "completed": [Issue.STATUS_VERIFIED, Issue.STATUS_CLOSED, Issue.STATUS_SPAM],        # finished
}


def _admin(request: HttpRequest):
    user = _current(request)
    return user if user.role == UserProfile.ROLE_ADMIN else None


@login_required_json
def admin_complaints(request: HttpRequest) -> JsonResponse:
    user = _admin(request)
    if not user:
        return _err("Admin only", 403)
    state = request.GET.get("state", "all")
    qs = Issue.objects.all()
    if state in STATE_GROUPS:
        qs = qs.filter(status__in=STATE_GROUPS[state])
    elif state != "all":
        return _err("state must be pending, active, completed or all", 400)
    counts = {k: Issue.objects.filter(status__in=v).count() for k, v in STATE_GROUPS.items()}
    pending_reports = [{"id": r.id, "text": r.raw_text, "student": r.user.name, "cmsId": r.user.code,
                        "at": r.created_at.isoformat(), "mention": (r.triage or {}).get("place_mention")}
                       for r in ReportEntry.objects.filter(pending=True).select_related("user").order_by("-created_at")]
    counts["awaiting_place"] = len(pending_reports)
    return JsonResponse({"state": state, "definitions": {
        "pending": "open or reopened: waiting for staff", "active": "in progress, or resolved / no issue and waiting for students to confirm",
        "completed": "verified by students, closed, or rejected as spam"},
        "counts": counts, "issues": [issue_json(i, user) for i in sorted(qs, key=lambda i: -i.updated_at.timestamp())],
        "awaitingPlace": pending_reports if state in ("pending", "all") else []})


@login_required_json
def admin_reports(request: HttpRequest):
    """Weekly (last 7 days) or monthly (last 30 days) report; CSV download or JSON."""
    user = _admin(request)
    if not user:
        return _err("Admin only", 403)
    period = request.GET.get("period", "weekly")
    if period not in ("weekly", "monthly"):
        return _err("period must be weekly or monthly", 400)
    end = timezone.now()
    start = end - timedelta(days=7 if period == "weekly" else 30)
    issues = list(Issue.objects.filter(created_at__gte=start).select_related("resolved_by").order_by("created_at"))
    reports_in = ReportEntry.objects.filter(created_at__gte=start)
    by_dept = {d.code: {"new": 0, "completed": 0, "open": 0} for d in Department.objects.all()}
    by_dept["CONFIDENTIAL"] = {"new": 0, "completed": 0, "open": 0}
    for i in issues:
        row = by_dept.setdefault(i.department, {"new": 0, "completed": 0, "open": 0})
        row["new"] += 1
        row["completed" if i.status in STATE_GROUPS["completed"] else "open"] += 1
    summary = {
        "period": period, "from": start.date().isoformat(), "to": end.date().isoformat(),
        "complaints_received": reports_in.count(), "issues_created": len(issues),
        "by_status": {k: sum(1 for i in issues if i.status in v) for k, v in STATE_GROUPS.items()},
        "critical_hazards": sum(1 for i in issues if i.priority == "CRITICAL"),
        "reopened_after_dispute": sum(1 for i in issues if i.reopen_count > 0),
        "by_department": by_dept,
        "staff_ratings": {s.staff_id: {"name": s.name, "department": s.department_id, "rating": s.profile.trust_score}
                          for s in Staff.objects.select_related("profile")},
    }
    if request.GET.get("format", "csv") == "json":
        return JsonResponse({"summary": summary, "issues": [issue_json(i, user) for i in issues]})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"ReportFix {period} report", f"{summary['from']} to {summary['to']}"])
    w.writerow(["Complaints received", summary["complaints_received"], "Issues created", summary["issues_created"],
                "Critical hazards", summary["critical_hazards"], "Reopened after dispute", summary["reopened_after_dispute"]])
    w.writerow(["Pending", summary["by_status"]["pending"], "Active", summary["by_status"]["active"],
                "Completed", summary["by_status"]["completed"]])
    w.writerow([])
    w.writerow(["Department", "New issues", "Completed", "Still open"])
    for dept, row in by_dept.items():
        w.writerow([dept, row["new"], row["completed"], row["open"]])
    w.writerow([])
    w.writerow(["Staff ID", "Name", "Department", "Rating"])
    for sid, row in summary["staff_ratings"].items():
        w.writerow([sid, row["name"], row["department"] or "unassigned", row["rating"]])
    w.writerow([])
    w.writerow(["Issue", "Title", "Total reports", "Place", "Department", "Priority", "Status", "Created", "Last update",
                "Handled by", "Reopened"])
    for i in issues:
        w.writerow([i.issue_code, i.general_title, i.report_count, "Confidential" if i.confidential else i.location,
                    i.department, i.priority, i.status, i.created_at.strftime("%Y-%m-%d %H:%M"),
                    i.updated_at.strftime("%Y-%m-%d %H:%M"), i.resolved_by.name if i.resolved_by else "", i.reopen_count])
    resp = HttpResponse(buf.getvalue(), content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="reportfix_{period}_{summary["to"]}.csv"'
    return resp


def _staff_json(s: Staff) -> Dict:
    acct = PortalAccount.objects.filter(user=s.profile).first()
    return {"staffId": s.staff_id, "name": s.name, "email": s.email, "phone": s.phone, "designation": s.designation,
            "department": s.department_id, "rating": s.profile.trust_score, "active": s.is_active,
            "joinedOn": s.joined_on.isoformat() if s.joined_on else None,
            "lastLogin": acct.last_login_at.isoformat() if acct and acct.last_login_at else None,
            "resolvedCount": s.profile.resolved_issues.count()}


@login_required_json
def admin_staff(request: HttpRequest) -> JsonResponse:
    if not _admin(request):
        return _err("Admin only", 403)
    if request.method == "POST":
        b = _body(request)
        try:
            staff, raw = engine.add_staff(str(b.get("name", "")), b.get("department") or None, str(b.get("designation", "")),
                                          str(b.get("email", "")), str(b.get("phone", "")))
        except ValueError as e:
            return _err(str(e), 400)
        return JsonResponse({"staff": _staff_json(staff),
                             "credentials": {"username": staff.staff_id, "email": staff.email, "password": raw,
                                             "note": "Shown once. The staff member signs in with Staff ID or email."}})
    include_removed = request.GET.get("all") == "1"
    qs = Staff.objects.select_related("profile").order_by("department_id", "staff_id")
    if not include_removed:
        qs = qs.filter(is_active=True)
    return JsonResponse({"staff": [_staff_json(s) for s in qs],
                         "departments": [{"code": d.code, "name": d.name} for d in Department.objects.all()]})


@login_required_json
def admin_staff_assign(request: HttpRequest, staff_id: str) -> JsonResponse:
    if not _admin(request):
        return _err("Admin only", 403)
    bad = _method(request, "POST")
    if bad:
        return bad
    try:
        staff = engine.assign_staff(staff_id, _body(request).get("department") or None)
    except LookupError as e:
        return _err(str(e), 404)
    except ValueError as e:
        return _err(str(e), 400)
    return JsonResponse({"staff": _staff_json(staff),
                         "message": f"{staff.name} now handles {staff.department_id or 'no department'}; "
                                    f"their next sign-in opens that queue."})


@login_required_json
def admin_staff_remove(request: HttpRequest, staff_id: str) -> JsonResponse:
    if not _admin(request):
        return _err("Admin only", 403)
    bad = _method(request, "POST")
    if bad:
        return bad
    restore = bool(_body(request).get("restore"))
    try:
        staff = engine.remove_staff(staff_id, restore=restore)
    except LookupError as e:
        return _err(str(e), 404)
    return JsonResponse({"staff": _staff_json(staff),
                         "message": f"{staff.name} {'restored' if restore else 'removed: can no longer sign in (history kept)'}."})


@login_required_json
def admin_students(request: HttpRequest) -> JsonResponse:
    if not _admin(request):
        return _err("Admin only", 403)
    rows = []
    for st in Student.objects.select_related("profile").order_by("name"):
        rows.append({"cmsId": st.cms_id, "name": st.name, "email": st.email, "phone": st.phone, "program": st.program,
                     "semester": st.semester, "section": st.section, "trust": st.profile.trust_score,
                     "complaints": st.profile.submitted_reports.count(),
                     "issues": st.profile.affected_issues.filter(confidential=False).count()})
    return JsonResponse({"students": rows})


@login_required_json
def departments(request: HttpRequest) -> JsonResponse:
    _current(request)
    return JsonResponse({"departments": [{"code": d.code, "name": d.name, "office": d.office, "email": d.email,
                                          "phone": d.phone, "head": d.head,
                                          "staff": [s.staff_id for s in d.staff.filter(is_active=True)]}
                                         for d in Department.objects.all()]})
