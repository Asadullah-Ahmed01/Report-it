import sys
from playwright.sync_api import sync_playwright
base = "http://127.0.0.1:8130"
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(base + "/"); pg.wait_for_load_state("networkidle")
    pg.screenshot(path="/home/claude/shots/0_landing_login.png")
    pg.click("text=Enter as student"); pg.wait_for_load_state("networkidle")
    pg.fill("textarea[name=text]", "Library ka AC bilkul thanda nahi kar raha"); pg.click("text=Send report"); pg.wait_for_load_state("networkidle")
    pg.screenshot(path="/home/claude/shots/1_student.png", full_page=False)
    pg.goto(base + "/board/"); pg.screenshot(path="/home/claude/shots/2_board.png")
    pg.select_option("select[name=code]", label=[o for o in pg.eval_on_selector_all("select[name=code] option", "els => els.map(e => e.textContent)") if "Library" in o][0])
    pg.wait_for_load_state("networkidle"); pg.screenshot(path="/home/claude/shots/3_staff.png")
    pg.select_option("select[name=code]", value="admin1"); pg.wait_for_load_state("networkidle")
    pg.screenshot(path="/home/claude/shots/4_admin.png")
    pg.goto(base + "/admin/staff/"); pg.screenshot(path="/home/claude/shots/5_admin_staff.png")

    code = pg.goto(base + "/admin/?state=all") and None
    b.close()
print("ok")
