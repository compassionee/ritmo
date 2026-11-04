"""Browser tests of the capture mode in About (only in personal test builds of the app, -PhelioCapture=true). The app
is simulated as in test_ui_backup.py: HELIO_ANDROID set before the page's scripts, /api/android/* answered here,
window.helioCapture called as Java calls it. Hidden on the server and in every build without the flag.
HELIO_SHOTS=<folder> also saves a screenshot of the section.
"""
import json
import os
import unittest

from test_ui import LOCALE, UI_LANG, UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

SHOTS = os.environ.get("HELIO_SHOTS")
AT = 1762201800
TYPES = [("56", "REST_HR"), ("46", "RECOVERY_HEART_RATE"), ("43", "BODY_BATTERY"), ("27", "SPO2_OSA_EVENT"),
         ("4c", "SEC_HR")]


def status(on=True, captured=True):
    types = []
    for code, name in TYPES:
        t = {"code": code, "name": name, "counts": {}, "bytes": 0, "last": None}
        if captured and code == "27":
            t.update(counts={"data": 2}, bytes=2048, last={"outcome": "data", "at": AT, "bytes": 1024})
        elif captured and code == "43":
            t.update(counts={"rejected": 2}, last={"outcome": "rejected", "at": AT, "bytes": 0, "status": 4})
        elif captured and code == "56":
            t.update(counts={"empty": 1}, last={"outcome": "empty", "at": AT, "bytes": 0})
        types.append(t)
    return {"available": True, "on": on, "files": 2 if captured else 0, "bytes": 2048 if captured else 0, "types": types}


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class CaptureServerUI(UIBase):
    def test_not_on_the_server(self):
        self.page.click("#avatar")
        self.page.click("#aboutOpen")
        expect(self.page.locator("#aboutSheet")).to_be_visible()
        expect(self.page.locator("#captureBox")).to_be_hidden()


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class CaptureAppUI(UIBase):
    def setUp(self):
        super().setUp()
        self.ctx.close()

    def open_app(self, capture):
        self.capture = capture
        self.actions = []
        self.ctx = self.browser.new_context(viewport={"width": 412, "height": 915}, timezone_id="Europe/Rome",
                                            locale=LOCALE, color_scheme="dark", service_workers="block")
        self.ctx.add_init_script("window.HELIO_ANDROID = true;")
        self.ctx.route("**/api/android/**", self.android)
        self.page = self.ctx.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(self.base)
        self.wait_catalog()
        self.page.click("#avatar")
        self.page.click("#aboutOpen")
        expect(self.page.locator("#aboutSheet")).to_be_visible()

    def android(self, route):
        req = route.request
        path = req.url.split("/api/android/", 1)[1].split("?")[0]
        body = {"capture": self.capture,
                "band": {"known": False, "connected": False, "status": "gb_stopped", "busy": None, "last_sync": None},
                "backup": {"on": False, "folder": None, "every": "week", "keep": 5, "running": False, "last_ok": None,
                           "last_file": None, "kept": None, "error": None, "error_at": None, "next": None},
                "reminders": {"permission": "ask", "asked": False, "next": {"day": None, "weigh": None}}}.get(path)
        if path == "action" and req.method == "POST":
            action = json.loads(req.post_data)["action"]
            self.actions.append(action)
            body = {"ok": True, "action": action}
        if body is None:
            route.fulfill(status=404, content_type="application/json", body="{}")
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def wait_actions(self, n):
        for _ in range(50):
            if len(self.actions) >= n:
                return
            self.page.wait_for_timeout(50)
        self.fail(f"actions: {self.actions}")

    def test_hidden_in_builds_without_the_flag(self):
        self.open_app({"available": False})
        self.page.wait_for_timeout(200)
        expect(self.page.locator("#captureBox")).to_be_hidden()

    def test_off_by_default_turn_on_and_see_what_came(self):
        self.open_app(status(on=False, captured=False))
        box = self.page.locator("#captureBox")
        expect(box).to_be_visible()
        box.locator("summary").click()
        expect(self.page.locator("#capOn")).not_to_be_checked()
        expect(self.page.locator("#capTypes li")).to_have_count(5)
        expect(self.page.locator("#capTypes li").first).to_contain_text("0x56 REST_HR")
        expect(self.page.locator("#capTypes li").first).to_contain_text(self.L("cap.never"))
        expect(self.page.locator("#capClear")).to_be_disabled()
        self.page.check("#capOn")
        self.wait_actions(1)
        self.assertEqual(self.actions, ["capture_on"])
        # after a sync, the app sends what was captured
        self.page.evaluate("(s) => window.helioCapture(s)", status())
        expect(self.page.locator("#capOn")).to_be_checked()
        expect(self.page.locator("#capSummary")).to_have_text(self.L("cap.summary", n=2, kb=2))
        rows = self.page.locator("#capTypes li")
        expect(rows.nth(3)).to_contain_text(self.L("cap.outcome.data"))
        expect(rows.nth(0)).to_contain_text(self.L("cap.outcome.empty"))
        expect(rows.nth(2)).to_contain_text(self.L("cap.outcome.rejected"))
        expect(rows.nth(2)).to_contain_text(f"{self.L('cap.outcome.rejected')} 2")
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"), width)
        if SHOTS:
            self.page.locator("#aboutSheet .sheet-body").screenshot(path=f"{SHOTS}/capture-{UI_LANG}.png")

    def test_export_and_clear_with_confirmation(self):
        self.open_app(status())
        self.page.locator("#captureBox summary").click()
        self.page.click("#capExport")
        self.wait_actions(1)
        self.assertEqual(self.actions, ["capture_export"])
        self.page.click("#capClear")  # first tap: asks for confirmation
        expect(self.page.locator("#capClear")).to_have_text(self.L("cat.remove_confirm"))
        self.page.wait_for_timeout(150)
        self.assertEqual(self.actions, ["capture_export"])
        self.page.click("#capClear")
        self.wait_actions(2)
        self.assertEqual(self.actions[-1], "capture_clear")
        self.page.evaluate("(s) => window.helioCapture(s)", status(captured=False))
        expect(self.page.locator("#capSummary")).to_have_text(self.L("cap.summary", n=0, kb=0))
        # turning off sends only that
        self.page.uncheck("#capOn")
        self.wait_actions(3)
        self.assertEqual(self.actions[-1], "capture_off")


if __name__ == "__main__":
    unittest.main()
