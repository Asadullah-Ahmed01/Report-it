"""Click-through test of every EzScene page for every role, with CSRF enforced (like a real browser)."""
import os, re, sys
sys.path.insert(0, sys.argv[1]); os.environ["DJANGO_SETTINGS_MODULE"] = "reportfix.settings"; os.environ["GEMINI_API_KEY"] = ""
import django; django.setup()
from django.test import Client, override_settings
from reportfix.engine import DjangoReportFixEngine
from reportfix.models import Staff, Student, Issue, UserProfile
DjangoReportFixEngine().reset_all_data()
R = []
def check(n, ok, d=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + ("" if ok else f" -> {str(d)[:240]}"))
c = Client(enforce_csrf_checks=True)
def tok(): return c.cookies["csrftoken"].value
def get(p, follow=True): return c.get(p, follow=follow)
def post(p, data=None, follow=True): return c.post(p, {**(data or {}), "csrfmiddlewaretoken": tok()}, follow=follow)
def html(r): return r.content.decode()
students = list(Student.objects.order_by("profile__id")); lib_staff = Staff.objects.get(department_id="Library")

r = get("/"); h = html(r)
check("landing page is the sign-in page", r.redirect_chain[-1][0] == "/login/" and "One log in for everyone" in h, r.redirect_chain)
r = get("/student/"); check("protected pages send you to sign in", r.redirect_chain[-1][0] == "/login/")
check("judge quick access on the sign-in page", "Judge quick access" in h and "Enter as student" in h)
r = post("/judge/switch/", {"code": Student.objects.order_by("profile__id").first().cms_id}); h = html(r)
check("EzScene wordmark + name on every page", "aria-label=\"EzScene\"" in h and "EzScene" in h)
check("judge bar: switcher with all 27 users, new student, reset", h.count("<option value=") >= 27 and "New student" in h and "Reset" in h)
r = post("/report/", {"text": "Library ka AC bilkul thanda nahi kar raha"}); h = html(r)
check("student submits report -> merged into 'AC fix', message shown", "AC fix" in h and ("Same problem" in h or "Created" in h), h[:300])
r = post("/report/", {"text": "Lab 99 ka projector kharab hai"}); h = html(r)
check("unknown place -> 'Where is this?' picker", "Where is this?" in h and "is not a place on the campus map" in h)
rid = re.search(r'/report/(\d+)/clarify/', h).group(1)
r = post(f"/report/{rid}/clarify/", {"place": "lab-1"}); h = html(r)
check("pick a place -> routed", "Lab 1" in h and r.status_code == 200)
r = get("/board/"); h = html(r); check("campus board: department tiles + trending", "Trending" in h and h.count('<a class="tile') == 6 and r.status_code == 200)
check("board never shows confidential cases", "Confidential welfare case" not in h)
code = Issue.objects.filter(confidential=False, status="open").exclude(affected_students=students[0].profile).first().issue_code
r = post(f"/issues/{code}/join/", {"next": "/board/"}); check("me too from the board", "Added you to" in html(r))
r = get(f"/issues/{code}/"); h = html(r); check("issue page: reasons, reports, history", "Why it's routed" in h and "Reports (" in h and "History" in h)
conf = Issue.objects.filter(confidential=True).first().issue_code
r = get(f"/issues/{conf}/"); check("student can't open someone else's confidential case", r.status_code == 403)
r = get(f"/people/{students[0].cms_id}/"); h = html(r); check("rating page explains every change", "Rating history" in h and "★" in h)
r = post("/judge/switch/", {"code": lib_staff.staff_id}); h = html(r)
check("judge switch to Library staff -> /staff/library/", r.redirect_chain[-1][0] == "/staff/library/" and "Central Library" in h, r.redirect_chain)
lib_issue = Issue.objects.filter(department="Library", status__in=["open", "in_progress"]).first().issue_code
r = post(f"/issues/{lib_issue}/status/", {"status": "resolved", "next": "/staff/library/"}); h = html(r)
check("staff marks fixed from the queue", "Updated" in h and "resolved" in h)
r = get("/staff/library/?tab=waiting"); check("staff tabs (waiting for students)", lib_issue in html(r))
other = Issue.objects.filter(department="CSE").first().issue_code
r = get(f"/issues/{other}/"); check("Library staff can't open a CSE issue", r.status_code == 403)
r = get("/staff/cse/"); check("Library staff typing /staff/cse/ is sent back to Library", r.redirect_chain[-1][0] == "/staff/library/")
r = post("/judge/switch/", {"code": students[0].cms_id})
r = get("/student/"); h = html(r)
check("affected student sees the vote buttons on a fixed issue", "Is it really fixed?" in h or "Yes, it's fixed" in h)
r = post(f"/issues/{lib_issue}/vote/", {"value": "dispute", "next": "/student/"}); check("student votes from the dashboard", "Recorded &#x27;dispute&#x27;" in html(r))
r = post("/judge/switch/", {"code": "admin1"}); h = html(r)
check("admin lands on the overview", r.redirect_chain[-1][0] == "/admin/" and "Pending (" in h and "Open safety hazards" in h)
for st in ["pending", "active", "completed", "all"]:
    r = get(f"/admin/?state={st}"); check(f"admin tab '{st}' renders", r.status_code == 200 and "Open by department" in html(r))
r = get("/admin/staff/"); h = html(r); check("admin staff page lists 6 staff", h.count('class="mono"') >= 6 and "Add staff" in h)
r = post("/admin/staff/", {"action": "add", "name": "Nadia Hussain", "designation": "Library Assistant", "department": "Library"}); h = html(r)
m = re.search(r"Sign-in: (STF\d{4}) \(or ([^)]+)\) / (\S+) \(shown once\)", h)
check("admin adds staff -> Staff ID + one-time password shown", bool(m), h[:400])
sid, email, pw = m.group(1), m.group(2), m.group(3)
r = post("/admin/staff/", {"action": "assign", "staff_id": sid, "department": "EE"}); check("admin assigns department", "now handles EE" in html(r))
r = get("/admin/students/?q=" + students[1].cms_id); h = html(r); check("admin student search by CMS ID", students[1].name in h and students[1].email in h)
r = get("/admin/reports/"); check("reports page", "Download weekly CSV" in html(r))
r = c.get("/api/admin/reports?period=monthly"); check("monthly CSV downloads", r["Content-Type"] == "text/csv")
with override_settings(JUDGE_MODE=False):
    x = Client(enforce_csrf_checks=True)
    r = x.get("/student/", follow=True); check("judge mode off: pages require sign-in", r.redirect_chain[-1][0] == "/login/")
    h = r.content.decode(); check("login page: no judge tools when judge mode is off", "Judge quick access" not in h and "Log in" in h)
    r = x.post("/login/", {"username": email, "password": pw, "csrfmiddlewaretoken": x.cookies["csrftoken"].value}, follow=True)
    check("new staff signs in by email -> straight to the EE queue", r.redirect_chain[-1][0] == "/staff/ee/", r.redirect_chain)
    r = x.post("/login/", {"username": students[2].cms_id, "password": "wrong", "csrfmiddlewaretoken": x.cookies["csrftoken"].value})
    check("wrong password -> generic error", "Invalid username or password" in r.content.decode())
    r = x.post("/logout/", {"csrfmiddlewaretoken": x.cookies["csrftoken"].value}, follow=True); check("sign out", r.redirect_chain[-1][0] == "/login/")
y = Client(enforce_csrf_checks=True); y.get("/login/")
r = y.post("/report/", {"text": "x"}); check("form post without CSRF token rejected", r.status_code == 403)
print(f"\n{sum(R)}/{len(R)} passed")
