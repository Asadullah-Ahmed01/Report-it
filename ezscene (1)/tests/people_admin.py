"""Tests for the new request: students with CMS IDs, staff with Staff IDs, seeded complaints, auto-portal login, admin tools."""
import csv, io, json, os, re, sys
sys.path.insert(0, sys.argv[1]); os.environ["DJANGO_SETTINGS_MODULE"] = "reportfix.settings"; os.environ["GEMINI_API_KEY"] = ""
import django; django.setup()
from django.test import Client, override_settings
from reportfix.models import Student, Staff, Department, Location, Issue, ReportEntry, PortalAccount, UserProfile
R = []
def check(n, ok, d=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + ("" if ok else f" -> {str(d)[:300]}"))
c = Client()
def post(cl, p, b=None): r = cl.post(p, data=json.dumps(b or {}), content_type="application/json"); return r.status_code, (r.json() if r["Content-Type"].startswith("application/json") else r)
def get(cl, p): r = c.get(p) if cl is None else cl.get(p); return r.status_code, (r.json() if r["Content-Type"].startswith("application/json") else r)
post(c, "/api/reset")

# ---- people ----
st = list(Student.objects.select_related("profile").order_by("profile__id"))
check("20 students", len(st) == 20, len(st))
check("CMS ID is a 5-digit primary key, unique", all(re.fullmatch(r"\d{5}", s.cms_id) for s in st) and len({s.cms_id for s in st}) == 20)
check("email = name + CMS ID + @mcs.nust.pk", all(s.email == "".join(s.name.lower().split()) + s.cms_id + "@mcs.nust.pk" for s in st), st[0].email)
check("phone = 11 digits starting with 55", all(re.fullmatch(r"55\d{9}", s.phone) for s in st), st[0].phone)
check("students have program, semester, section", all(s.program and 1 <= s.semester <= 8 and s.section for s in st))
sf = list(Staff.objects.select_related("profile"))
check("6 staff, one per department", len(sf) == 6 and {s.department_id for s in sf} == {"EE", "CSE", "IS", "Library", "Cafeteria", "Labs"})
check("Staff ID is the primary key (STFnnnn)", all(re.fullmatch(r"STF\d{4}", s.staff_id) for s in sf))
check("staff have email, 55-phone, designation, joining date", all(s.email.endswith("@mcs.nust.pk") and re.fullmatch(r"55\d{9}", s.phone) and s.designation and s.joined_on for s in sf))
check("6 departments with office, email, phone, head", Department.objects.count() == 6 and all(d.office and d.email and d.phone and d.head for d in Department.objects.all()))
check("campus locations stored, each owned by a department", Location.objects.count() >= 20)
check("login username = CMS ID / Staff ID", all(PortalAccount.objects.get(user=s.profile).username == s.cms_id for s in st) and
      all(PortalAccount.objects.get(user=s.profile).username == s.staff_id.lower() for s in sf))
check("same IDs after every reset (reproducible)", (post(c, "/api/reset"), sorted(Student.objects.values_list("cms_id", flat=True)) == sorted(s.cms_id for s in st))[1])

# ---- complaints seeded with redundancy ----
ayesha = UserProfile.objects.get(code=st[0].cms_id)
lib = Issue.objects.get(group_key="library:ac")
check("same student reports the same issue twice (Ayesha, library AC): count stays 3", lib.reporters.filter(pk=ayesha.pk).exists() and
      ReportEntry.objects.filter(user=ayesha, issue=lib).count() == 2 and lib.report_count == 3, lib.display_title)
check("several students -> one issue (Server room WiFi, total reports: 3)", Issue.objects.get(group_key="server-room:wifi").report_count == 3)
check("same student reports several different issues (Ayesha: 3)", ayesha.affected_issues.count() >= 3, ayesha.affected_issues.count())
states = set(Issue.objects.values_list("status", flat=True))
check("seeded complaints cover every state", {"open", "in_progress", "resolved", "verified", "reopened", "closed", "spam"} <= states, states)
check("a report waiting for a place, and a confidential case", ReportEntry.objects.filter(pending=True).exists() and Issue.objects.filter(confidential=True).exists())

# ---- login auto-detect ----
s0, f0 = st[0], sf[0]
for ident, kind in [(s0.cms_id, "CMS ID"), (s0.email, "email")]:
    lc = Client(); s, d = post(lc, "/api/auth/login", {"username": ident, "password": "CodeQuest26!"})
    check(f"student signs in with {kind} -> student portal", s == 200 and d["portal"] == "student" and d["redirect"] == "/student/", d)
lib_staff = Staff.objects.get(department_id="Library", is_active=True)
for ident, kind in [(lib_staff.staff_id, "Staff ID"), (lib_staff.email, "email")]:
    lc = Client(); s, d = post(lc, "/api/auth/login", {"username": ident, "password": "CodeQuest26!"})
    check(f"staff signs in with {kind} -> straight to the Library queue", s == 200 and d["portal"] == "staff" and d["department"] == "Library" and d["redirect"] == "/staff/library/", d)
lc = Client(); s, d = post(lc, "/api/auth/login", {"username": "registrar@mcs.nust.pk", "password": "Registrar@26"})
check("admin signs in with email -> admin portal", s == 200 and d["portal"] == "admin", d)

# ---- admin: complaints by state ----
adm = Client(); post(adm, "/api/auth/login", {"username": "admin1", "password": "Registrar@26"})
seen = {}
for state in ["pending", "active", "completed"]:
    s, d = get(adm, f"/api/admin/complaints?state={state}"); seen[state] = {i["status"] for i in d["issues"]}
check("admin: pending complaints (open / reopened)", seen["pending"] and seen["pending"] <= {"open", "reopened"}, seen["pending"])
check("admin: active complaints (in progress / awaiting student confirmation)", seen["active"] and seen["active"] <= {"in_progress", "resolved", "no_issue"}, seen["active"])
check("admin: completed complaints (verified / closed / spam)", seen["completed"] and seen["completed"] <= {"verified", "closed", "spam"}, seen["completed"])
s, d = get(adm, "/api/admin/complaints?state=pending")
check("admin: reports waiting for a place listed under pending", len(d["awaitingPlace"]) == 1)
s, d = get(adm, "/api/admin/students")
check("admin: student list with CMS ID, email, phone, trust, complaint count", len(d["students"]) == 20 and all(k in d["students"][0] for k in ("cmsId", "email", "phone", "trust", "complaints")))

# ---- admin: weekly / monthly reports ----
s, resp = get(adm, "/api/admin/reports?period=weekly")
txt = resp.content.decode()
check("weekly report downloads as CSV", s == 200 and resp["Content-Type"] == "text/csv" and "attachment" in resp["Content-Disposition"], resp["Content-Disposition"] if s == 200 else s)
s, wk = get(adm, "/api/admin/reports?period=weekly&format=json"); s, mo = get(adm, "/api/admin/reports?period=monthly&format=json")
check("weekly covers 7 days, monthly 30 (monthly has more)", wk["summary"]["issues_created"] < mo["summary"]["issues_created"], (wk["summary"]["issues_created"], mo["summary"]["issues_created"]))
check("40-day-old complaint excluded from the monthly report", "Physics Lab" not in json.dumps(mo["issues"]))
rows = list(csv.reader(io.StringIO(txt)))
check("CSV has per-department, staff rating and issue sections", any(r[:1] == ["Department"] for r in rows) and any(r[:1] == ["Staff ID"] for r in rows) and any(r[:1] == ["Issue"] for r in rows))
st_c = Client(); post(st_c, "/api/auth/login", {"username": s0.cms_id, "password": "CodeQuest26!"})
s, _ = get(st_c, "/api/admin/reports?period=weekly"); check("students can't download reports", s == 403)

# ---- admin: add / assign / remove staff ----
s, d = post(adm, "/api/admin/staff", {"name": "Nadia Hussain", "department": "Library", "designation": "Library Assistant"})
new = d["staff"]; cred = d["credentials"]
check("admin adds staff -> new Staff ID + one-time password", s == 200 and re.fullmatch(r"STF\d{4}", new["staffId"]) and cred["password"], d)
s, d = post(adm, f"/api/admin/staff/{new['staffId']}/assign", {"department": "CSE"})
check("admin assigns a department", s == 200 and d["staff"]["department"] == "CSE", d)
nc = Client(); s, d = post(nc, "/api/auth/login", {"username": new["staffId"], "password": cred["password"]})
check("assigned staff signs in -> straight to the CSE queue", s == 200 and d["redirect"] == "/staff/cse/" and d["department"] == "CSE", d)
s, q = get(nc, "/api/issues?view=queue"); check("...and sees only CSE issues", all(i["department"] == "CSE" for i in q["issues"]) and len(q["issues"]) >= 1)
post(adm, f"/api/admin/staff/{new['staffId']}/assign", {"department": "Library"})
s, q = get(nc, "/api/issues?view=queue"); check("reassigning moves a signed-in staff member to the Library queue at once", q["issues"] and all(i["department"] == "Library" for i in q["issues"]))
s, d = post(adm, f"/api/admin/staff/{new['staffId']}/remove")
check("admin removes staff", s == 200 and d["staff"]["active"] is False)
s, q = get(nc, "/api/issues?view=queue"); check("removed staff loses staff access at once", s in (401, 403) and "issues" not in q, s)
nc2 = Client(); s, d = post(nc2, "/api/auth/login", {"username": new["staffId"], "password": cred["password"]})
check("removed staff can't sign in", s == 403, d)
s, u = get(adm, "/api/users"); check("removed staff hidden from the user switcher", new["staffId"] not in json.dumps(u))
s, d = post(adm, f"/api/admin/staff/{new['staffId']}/remove", {"restore": True}); check("admin can restore staff", d["staff"]["active"] is True)
s, d = post(adm, "/api/admin/staff", {"name": "No Dept Person"}); nd = d["credentials"]
x = Client(); s, d = post(x, "/api/auth/login", {"username": nd["username"], "password": nd["password"]})
check("staff with no department yet -> told to ask the admin, empty queue", d["redirect"] == "/staff/unassigned/" and get(x, "/api/issues?view=queue")[1]["issues"] == [], d)
s, _ = post(st_c, "/api/admin/staff", {"name": "Hacker"}); check("students can't add staff", s == 403)
s, _ = post(st_c, f"/api/admin/staff/{lib_staff.staff_id}/remove"); check("students can't remove staff", s == 403)
print(f"\n{sum(R)}/{len(R)} passed")
