import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["DJANGO_SETTINGS_MODULE"] = "reportfix.settings"
os.environ["GEMINI_API_KEY"] = ""
import django; django.setup()
from reportfix.engine import DjangoReportFixEngine as _E
_E().reset_all_data()
from reportfix.models import UserProfile as _UP, Staff as _SF
_st = list(_UP.objects.filter(role="student").order_by("id").values_list("code", flat=True))
U = {f"s{i+1}": _st[i] for i in range(6)}
_DK = {"CSE": "staff_cse", "EE": "staff_ee", "IS": "staff_is", "Library": "staff_lib", "Cafeteria": "staff_caf", "Labs": "staff_labs"}
for _s in _SF.objects.all():
    U[_DK[_s.department_id]] = _s.staff_id

from django.test import Client, override_settings
from reportfix.models import PortalAccount, LoginAttempt
results = []
def check(name, cond, detail=""):
    results.append(cond); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" -> {detail}"))
def post(c, path, body=None, **kw):
    r = c.post(path, data=json.dumps(body or {}), content_type="application/json", **kw); return r.status_code, r.json()
def get(c, path):
    r = c.get(path); return r.status_code, r.json()

c = Client(); post(c, "/api/reset")
with override_settings(JUDGE_MODE=False):
    a = Client()
    s, d = get(a, "/api/issues?view=board"); check("judge off: no session -> 401", s == 401, (s, d))
    a.cookies["rf_uid"] = "admin1"
    s, d = get(a, "/api/issues?view=all"); check("forged rf_uid=admin1 cookie ignored", s == 401, (s, d))
    s, d = post(a, "/api/session", {"userId": "admin1"}); check("judge off: one-click switch refused", s == 403, (s, d))
    s1, d1 = post(a, "/api/auth/login", {"portal": "student", "username": U["s1"], "password": "wrong"})
    s2, d2 = post(a, "/api/auth/login", {"portal": "student", "username": "nobody", "password": "wrong"})
    check("same message for wrong password and unknown user", s1 == s2 == 401 and d1["error"] == d2["error"], (d1, d2))
    s, d = post(a, "/api/auth/login", {"portal": "admin", "username": U["s1"], "password": "CodeQuest26!"})
    check("student credential on admin portal -> 403", s == 403 and "student portal" in d["error"], d)
    s, d = post(a, "/api/auth/login", {"portal": "staff", "username": "admin1", "password": "Registrar@26"})
    check("admin credential on staff portal -> 403", s == 403, d)
    before = a.cookies.get("sessionid").value if a.cookies.get("sessionid") else None
    s, d = post(a, "/api/auth/login", {"portal": "student", "username": U["s1"], "password": "CodeQuest26!"})
    check("student signs in to student portal", s == 200 and d["user"]["code"] == U["s1"], d)
    check("session id rotated at sign-in", a.cookies.get("sessionid").value != before)
    s, d = get(a, "/api/issues?view=all"); check("signed-in student can't use admin view", s == 403, s)
    s, d = get(a, "/api/admin/accounts"); check("student can't read accounts table", s == 403, s)
    s, d = post(a, "/api/reset"); check("student can't reset when judge mode off", s == 403, s)
    s, d = post(a, "/api/auth/logout"); s, d = get(a, "/api/issues?view=mine"); check("logout ends session", s == 401, s)
    b = Client()
    s, d = post(b, "/api/auth/login", {"portal": "staff", "username": U["staff_cse"], "password": "CodeQuest26!"})
    s, d = get(b, "/api/issues?view=queue")
    check("staff portal: CSE staff sees only CSE queue", s == 200 and all(i["department"] == "CSE" for i in d["issues"]), d)
    lc = Client()
    for _ in range(5):
        post(lc, "/api/auth/login", {"portal": "student", "username": U["s2"], "password": "bad"})
    s, d = post(lc, "/api/auth/login", {"portal": "student", "username": U["s2"], "password": "CodeQuest26!"})
    check("lockout after 5 failures (even with right password)", s == 429, (s, d))
    adm = Client()
    s, d = post(adm, "/api/auth/login", {"portal": "admin", "username": "admin1", "password": "Registrar@26"})
    s, d = get(adm, "/api/admin/accounts")
    check("admin sees accounts with portal per credential", s == 200 and {a["portal"] for a in d["accounts"]} == {"student", "staff", "admin"}, d)
    check("no password hash in accounts payload", "pbkdf2" not in json.dumps(d), "leak")
    s, d = post(adm, "/api/users", {"name": "Fresh"})
    cred = d.get("credentials", {})
    nc = Client()
    s, d = post(nc, "/api/auth/login", {"portal": "student", "username": cred.get("username"), "password": cred.get("password")})
    check("admin-created student can sign in with one-time password", s == 200, (s, d))
acct = PortalAccount.objects.get(username=U["s1"])
check("passwords stored as salted PBKDF2 hashes", acct.password_hash.startswith("pbkdf2_sha256$") and "CodeQuest26!" not in acct.password_hash)
check("every sign-in attempt logged", LoginAttempt.objects.filter(success=False).count() >= 8 and LoginAttempt.objects.filter(success=True).exists())
cs = Client(enforce_csrf_checks=True)
r = cs.post("/api/auth/login", data=json.dumps({"portal": "student", "username": U["s1"], "password": "CodeQuest26!"}), content_type="application/json")
check("POST without CSRF token rejected", r.status_code == 403, r.status_code)
page = cs.get("/"); token = cs.cookies["csrftoken"].value
r = cs.post("/api/auth/login", data=json.dumps({"portal": "student", "username": U["s3"], "password": "CodeQuest26!"}), content_type="application/json", HTTP_X_CSRFTOKEN=token)
check("POST with CSRF token from the page works", r.status_code == 200, (r.status_code, r.content[:200]))
j = Client(); s, d = get(j, "/api/session")
check("judge mode on: one-click works without password", s == 200 and d["judgeMode"] is True, d)
print(f"\n{sum(results)}/{len(results)} passed")
