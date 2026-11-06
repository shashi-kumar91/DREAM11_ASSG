"""Capture docs/screenshots against a running server (see docs/screenshots/README.md)."""
import json, pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parent / "screenshots"
B = "http://127.0.0.1:8931"
report = {"shots": [], "console_errors": [], "notes": {}}

def shot(pg, name, wait=600):
    pg.wait_for_timeout(wait)
    pg.screenshot(path=str(OUT / name), full_page=False)
    report["shots"].append(name)

def scroll_to(pg, sel, offset=-90):
    if pg.locator(sel).count():
        box = pg.locator(sel).first.bounding_box()
        if box:
            pg.evaluate("(args) => window.scrollTo(0, Math.max(0, args.y + window.scrollY + args.off))",
                        {"y": box["y"], "off": offset})

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = b.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    pg.on("console", lambda m: report["console_errors"].append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: report["console_errors"].append("pageerror: " + str(e)))

    pg.goto(B + "/", wait_until="networkidle"); shot(pg, "home.png", 900)

    # gap: recorded scenario, answer "I left at 16.2", then frame the receipt itself
    pg.goto(B + "/gap", wait_until="networkidle")
    pg.click("button:has-text('Run scenario S1')")
    pg.wait_for_selector(".ask-option", timeout=60000)
    shot(pg, "gap-question.png", 400)
    # One answer. The product asks once on purpose; clicking a second card that the model
    # offered afterwards is a path this demo does not claim to be recorded for.
    pg.locator(".ask-option").nth(0).click()
    pg.wait_for_timeout(12000)
    report["notes"]["gap_source"] = pg.inner_text(".answer-source").replace("\n", " | ") if pg.locator(".answer-source").count() else "MISSING"
    scroll_to(pg, "h2:has-text('Your catch-up receipt')", -110)
    shot(pg, "gap-run.png", 700)
    if pg.locator("button:has-text('Show the working')").count():
        pg.locator("button:has-text('Show the working')").first.click()
        pg.wait_for_timeout(700)
        scroll_to(pg, "text=Show the working" if False else ".under-hood, .panel:has-text('working')", 0)
    shot(pg, "gap-behind.png", 500)

    # custom gap case, live pool
    pg.goto(B + "/gap", wait_until="networkidle")
    pg.select_option("select[aria-label='resume ball']", index=8)
    pg.select_option("select[aria-label='what happened']", "ad")
    pg.click("button:has-text('Run my own situation')")
    for _ in range(80):
        pg.wait_for_timeout(1500)
        if pg.locator(".ask-option").count():
            pg.locator(".ask-option").first.click()
            break
    pg.wait_for_timeout(25000)
    report["notes"]["custom_source"] = pg.inner_text(".answer-source").replace("\n", " | ") if pg.locator(".answer-source").count() else "MISSING"
    scroll_to(pg, "h2:has-text('Your catch-up receipt')", -110)
    shot(pg, "gap-custom.png", 700)

    # creator: all 12 segments
    pg.goto(B + "/creator", wait_until="networkidle")
    pg.click("button:has-text('Check all 12')")
    pg.wait_for_timeout(30000)
    scroll_to(pg, "h2:has-text('Committed')" if pg.locator("h2:has-text('Committed')").count() else ".card", -110)
    shot(pg, "creator-run.png", 800)

    # creator: a line the feed contradicts
    pg.goto(B + "/creator", wait_until="networkidle")
    pg.fill("input[aria-label='type what the creator said']", "142 for 2, yeh match abhi bhi khula hai")
    pg.click("button:has-text('Check this line')")
    pg.wait_for_timeout(25000)
    scroll_to(pg, ".card, h2:has-text('Committed')", -110)
    shot(pg, "creator-custom.png", 700)

    # agents
    pg.goto(B + "/agents", wait_until="networkidle")
    if pg.locator("button:has-text('Race all three situations')").count():
        pg.locator("button:has-text('Race all three situations')").first.click()
    pg.wait_for_timeout(15000)
    shot(pg, "agents.png", 800)

    m = b.new_context(viewport={"width": 414, "height": 896}, device_scale_factor=2, is_mobile=True, has_touch=True)
    mp = m.new_page()
    mp.goto(B + "/gap", wait_until="networkidle")
    mp.click("button:has-text('Run scenario S1')")
    try:
        mp.wait_for_selector(".ask-option", timeout=60000)
        mp.locator(".ask-option").nth(1).click()
    except Exception:
        pass
    mp.wait_for_timeout(14000)
    mp.screenshot(path=str(OUT / "mobile-gap.png"))
    report["shots"].append("mobile-gap.png")
    b.close()

report["console_error_count"] = len(report["console_errors"])
report["console_errors"] = report["console_errors"][:5]
print(json.dumps(report, indent=1))
