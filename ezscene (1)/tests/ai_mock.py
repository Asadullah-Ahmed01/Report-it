import io, json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["DJANGO_SETTINGS_MODULE"] = "reportfix.settings"
os.environ["GEMINI_API_KEY"] = "fake-key-for-test"
import django; django.setup()
import urllib.request
from reportfix import ai
from reportfix.engine import DjangoReportFixEngine
captured = {}
def fake_urlopen(req, timeout=0):
    body = json.loads(req.data)
    captured["url"] = req.full_url; captured["headers"] = dict(req.headers); captured["body"] = body
    schema = body["generationConfig"].get("responseSchema")
    if schema:
        cands = schema["properties"]["same_as_issue_id"]["enum"]
        text = body["contents"][0]["parts"][0]["text"]
        # pretend the model merges "net nahi" into the open Lab 3 wifi issue if present
        out = {"place_id": "lab-3", "place_mention": "lab 3", "category": "wifi", "hazard": "none",
               "confidential": False, "injection": False,
               "same_as_issue_id": next((c for c in cands if c != "none"), "none") if "net" in text else "none",
               "summary": "WiFi not working", "reason": "Student says the internet in Lab 3 is down."}
        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(out)}]}}]}
    else:
        payload = {"candidates": [{"content": {"parts": [{"text": "- All good."}]}}]}
    return io.BytesIO(json.dumps(payload).encode())
class Ctx(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
urllib.request.urlopen = lambda req, timeout=0: Ctx(fake_urlopen(req, timeout).getvalue())
e = DjangoReportFixEngine(); e.reset_all_data()
r1 = e.submit_report("s1", "Lab 3 ka wifi band hai")
print("r1", r1["outcome"], r1["issue"].issue_code, r1["issue"].department, r1["issue"].priority, r1["report"].source)
r2 = e.submit_report("s2", "lab3 mein net nahi chal raha")
print("r2", r2["outcome"], r2["issue"].issue_code, "me-too", r2["issue"].me_too_count)
print("url", captured["url"]); print("has key header", "X-goog-api-key" in captured["headers"])
print("thinking", captured["body"]["generationConfig"].get("thinkingConfig"))
print("summary", e.admin_summary_with_source())
print("status", ai.STATUS)
