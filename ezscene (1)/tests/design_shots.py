import sys, glob, os
from playwright.sync_api import sync_playwright
src = sys.argv[1]
want = {0: (1280, 1160), 1: (1280, 960), 5: (1280, 1180), 11: (1280, 1140), 12: (1280, 1080), 25: (1280, 1100), 16: (1280, 1140), 30: (1920, 1080), 31: (1920, 1080), 32: (1920, 1080), 29: (1920, 1080)}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    for idx, (w, h) in want.items():
        f = glob.glob(os.path.join(src, f"{idx}_*.html"))[0]
        pg = b.new_page(viewport={"width": w, "height": h})
        pg.goto("file://" + f); pg.wait_for_timeout(2500)
        pg.screenshot(path=f"/home/claude/design_shots/{idx}.png")
        pg.close()
    b.close()
print("ok")
