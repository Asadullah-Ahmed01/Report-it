import json, os, sys
sys.path.insert(0, sys.argv[1]); os.environ["DJANGO_SETTINGS_MODULE"] = "reportfix.settings"; os.environ["GEMINI_API_KEY"] = ""
import django; django.setup()
from reportfix.engine import DjangoReportFixEngine as _E
_E().reset_all_data()
from reportfix.models import UserProfile as _UP, Staff as _SF
_st = list(_UP.objects.filter(role="student").order_by("id").values_list("code", flat=True))
U = {f"s{i+1}": _st[i] for i in range(6)}
_DK = {"CSE": "staff_cse", "EE": "staff_ee", "IS": "staff_is", "Library": "staff_lib", "Cafeteria": "staff_caf", "Labs": "staff_labs"}
for _s in _SF.objects.all():
    U[_DK[_s.department_id]] = _s.staff_id

from django.test import Client
c = Client(); R = []
def check(n, ok, d=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + ("" if ok else f" -> {d}"))
def post(p, b=None): r = c.post(p, data=json.dumps(b or {}), content_type="application/json"); return r.status_code, r.json()
def get(p): r = c.get(p); return r.status_code, r.json()
def as_user(u): post("/api/session", {"userId": u})
def report(u, t): as_user(u); return post("/api/reports", {"text": t})
def rating(u): return get(f"/api/users/{u}/trust")[1]["trust"]

post("/api/reset"); as_user(U["s1"])
s, b = get("/api/issues?view=board")
ac = next((i for i in b["issues"] if i["generalTitle"] == "AC fix" and i["department"] == "Library"), {})
check("seed: 3 wordings -> one 'AC fix (total reports: 3)', Library", ac.get("displayTitle") == "AC fix (total reports: 3)", ac.get("displayTitle"))
s, d = report(U["s4"], "library ka AC kharab hai, garmi hai")
check("4th student -> same issue, total reports: 4", d["outcome"] == "merged" and d["issue"]["displayTitle"] == "AC fix (total reports: 4)", d.get("message"))
s, d = report(U["s1"], "library AC still not working")
check("same student again -> count stays 4", d["issue"]["totalReports"] == 4, d.get("message"))
s, d = report(U["s5"], "Library wifi is very slow")
check("different problem, same place -> separate 'WiFi fix'", d["outcome"] == "created" and d["issue"]["generalTitle"] == "WiFi fix", d)
s, d = report(U["s5"], "CSE classroom 5 ka projector kaam nahi kar raha")
check("CSE classroom 5 -> CSE, 'Projector fix'", d["issue"]["department"] == "CSE" and d["issue"]["place"] == "CSE Classroom 5" and d["issue"]["generalTitle"] == "Projector fix", d)
s, d = report(U["s6"], "projector in cse classroom 5 is dead")
check("same classroom + problem -> total reports: 2", d["outcome"] == "merged" and d["issue"]["totalReports"] == 2, d.get("message"))
s, d = report(U["s6"], "projector in cse classroom 7 is dead")
check("different classroom number -> separate issue", d["outcome"] == "created" and d["issue"]["place"] == "CSE Classroom 7", d.get("message"))
s, d = report(U["s4"], "EE room 12 mein fan nahi chal raha")
check("EE room 12 -> EE Classroom 12", d["issue"]["department"] == "EE" and d["issue"]["place"] == "EE Classroom 12", d)
s, d = report(U["s4"], "IS classroom AC band hai")
check("IS classroom -> IS", d["issue"]["department"] == "IS", d)
s, d = report(U["s3"], "classroom 3 ka fan kharab hai")
names = [p["name"] for p in d.get("suggestions", [])]
check("classroom with no department -> ask, offer only CSE/EE/IS classrooms", d["outcome"] == "needs_clarification" and sorted(names) == ["CSE Classroom", "EE Classroom", "IS Classroom"], (d["outcome"], names))
# staff finds no issue -> student rating falls
s, d = report(U["s2"], "Tuck shop ki table toot gayi hai")
code = d["issue"]["code"]; before_s2, before_staff = rating(U["s2"]), rating(U["staff_caf"])
as_user(U["staff_caf"]); s, d = post(f"/api/issues/{code}/status", {"status": "no_issue"})
check("staff 'no issue' -> student rating -10", rating(U["s2"]) == before_s2 - 10, (before_s2, rating(U["s2"])))
as_user(U["s2"]); s, d = post(f"/api/issues/{code}/vote", {"value": "dispute"})
check("student disputes 'no issue' -> reopened", d["issue"]["status"] == "reopened", d.get("message"))
check("...student rating restored (+10, +3 for right vote)", rating(U["s2"]) == before_s2 + 3, rating(U["s2"]))
check("...staff rating falls (-10)", rating(U["staff_caf"]) == before_staff - 10, rating(U["staff_caf"]))
# staff marks resolved but students say not fixed -> staff rating falls
ac_code = ac["code"]; before = rating(U["staff_lib"])
as_user(U["staff_lib"]); post(f"/api/issues/{ac_code}/status", {"status": "resolved"})
as_user(U["s1"]); post(f"/api/issues/{ac_code}/vote", {"value": "dispute"})
as_user(U["s2"]); s, d = post(f"/api/issues/{ac_code}/vote", {"value": "dispute"})
check("resolved but not fixed -> reopened", d["issue"]["status"] == "reopened", d.get("message"))
check("...staff rating -10 with reason", rating(U["staff_lib"]) == before - 10, rating(U["staff_lib"]))
s, t = get(f"/api/users/{U['staff_lib']}/trust")
check("...staff can see why", "not fixed" in t["log"][0]["reason"], t["log"][0])
# fixed for real -> staff rating rises
as_user(U["staff_lib"]); post(f"/api/issues/{ac_code}/status", {"status": "resolved"})
for u in [U["s1"], U["s2"], U["s3"]]:
    as_user(u); s, d = post(f"/api/issues/{ac_code}/vote", {"value": "confirm"})
check("students confirm -> verified", d["issue"]["status"] == "verified", d.get("message"))
check("...staff rating +5", rating(U["staff_lib"]) == before - 5, rating(U["staff_lib"]))
# login identifies portal from credentials
lc = Client()
r = lc.post("/api/auth/login", data=json.dumps({"username": U["staff_cse"], "password": "CodeQuest26!"}), content_type="application/json")
check("login without picking portal -> system identifies 'staff'", r.status_code == 200 and r.json()["portal"] == "staff", r.content[:200])
r = lc.post("/api/auth/login", data=json.dumps({"username": "admin1", "password": "Registrar@26"}), content_type="application/json")
check("admin credentials -> admin portal", r.status_code == 200 and r.json()["portal"] == "admin", r.content[:200])
r = lc.post("/api/auth/login", data=json.dumps({"portal": "admin", "username": U["s1"], "password": "CodeQuest26!"}), content_type="application/json")
check("student credentials refused on admin portal", r.status_code == 403, r.content[:200])
print(f"\n{sum(R)}/{len(R)} passed")
