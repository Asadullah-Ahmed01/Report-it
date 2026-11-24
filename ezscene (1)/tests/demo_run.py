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
c = Client()
def post(p, b=None): r = c.post(p, data=json.dumps(b or {}), content_type="application/json"); return r.json()
post("/api/reset")
students = [U["s1"], U["s2"], U["s3"], U["s4"], U["s5"], U["s6"]] + [post("/api/users", {"name": f"Student {n}"})["user"]["code"] for n in range(7, 15)]
# (student, text, expected department, expected place, expected title or "ASK")
cases = [
    (0, "Library ka AC kaam nahi kar raha", "Library", "Main Library", "AC fix"),
    (3, "the air conditioner in main library is blowing hot air", "Library", "Main Library", "AC fix"),
    (4, "AC not cooling in the libary", "Library", "Main Library", "AC fix"),
    (0, "library AC abhi bhi kharab hai", "Library", "Main Library", "AC fix"),  # same student again
    (1, "Cafeteria ka khana basi tha aaj", "Cafeteria", "Cafeteria", "Food quality issue"),
    (2, "food in the canteen was stale", "Cafeteria", "Cafeteria", "Food quality issue"),
    (5, "cafetria mein chai thandi aur gandi thi", "Cafeteria", "Cafeteria", "Food quality issue"),
    (6, "Server room ka internet down hai", "IS", "Server Room", "WiFi fix"),
    (7, "wifi not working in server room since morning", "IS", "Server Room", "WiFi fix"),
    (8, "Lab 2 ki keyboard kharab hai", "Labs", "Lab 2", "Computer fix"),
    (9, "lab2 PC wont turn on", "Labs", "Lab 2", "Computer fix"),
    (10, "EE lab 1 mein socket se chingari nikal rahi hai", "EE", "EE Lab 1", "Sparks / short circuit"),
    (11, "Reading hall ke washroom mein paani nahi aa raha", "Library", "Reading Hall", "Water / washroom fix"),
    (12, "Tuck shop pe table tooti hui hai", "Cafeteria", "Tuck Shop", "Furniture fix"),
    (1, "CSE classroom 3 ka projector nahi chal raha", "CSE", "CSE Classroom 3", "Projector fix"),
    (2, "projector in CSE class 3 is not working", "CSE", "CSE Classroom 3", "Projector fix"),
    (3, "cse room 3 projector kharab", "CSE", "CSE Classroom 3", "Projector fix"),
    (4, "CSE classroom 3 wifi slow hai", "CSE", "CSE Classroom 3", "WiFi fix"),
    (5, "EE classroom 2 mein AC band hai", "EE", "EE Classroom 2", "AC fix"),
    (6, "ee class 2 ka ac garam hawa de raha hai", "EE", "EE Classroom 2", "AC fix"),
    (7, "IS classroom 1 lights not working", "IS", "IS Classroom 1", "Electrical fix"),
    (8, "classroom 4 ki chairs tooti hain", None, None, "ASK"),
    (9, "Library ka classroom ka fan", "Library", "Main Library", "Fan fix"),
]
rows, ok_all = [], 0
for idx, text, dept, place, title in cases:
    post("/api/session", {"userId": students[idx]})
    d = post("/api/reports", {"text": text})
    if title == "ASK":
        sugg = sorted(p["name"] for p in d.get("suggestions", []))
        ok = d["outcome"] == "needs_clarification"
        got = f"asks for place: {', '.join(sugg)}"
    else:
        i = d.get("issue", {})
        ok = i.get("department") == dept and i.get("place") == place and i.get("generalTitle") == title
        got = f"{i.get('displayTitle')} | {i.get('place')} -> {i.get('department')} | {i.get('priority')} | {d['outcome']}"
    ok_all += ok
    rows.append((("OK " if ok else "XX "), students[idx], text, got))
for r in rows:
    print(f"{r[0]} {r[1]:>4} | {r[2][:52]:52} | {r[3]}")
print(f"\n{ok_all}/{len(cases)} matched expectations")
post("/api/session", {"userId": "admin1"})
print("\nFinal board (admin view):")
for i in post.__globals__["c"].get("/api/issues?view=all").json()["issues"]:
    print(f"  {i['code']:7} {i['displayTitle']:42} {i['place']:18} -> {i['department']:10} {i['priority']}")
