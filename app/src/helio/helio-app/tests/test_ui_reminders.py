"""Browser tests of the reminders card (Profile, only in the Android app). The app is simulated as in
test_ui_backup.py: HELIO_ANDROID set before the page's scripts, /api/android/* answered here, window.helioReminders
called as Java calls it; /api/settings is the real server, so settings.json is checked on disk.
HELIO_SHOTS=<folder> also saves a screenshot of the card.
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
NEXT_DAY = 1762201800    # some evening
NEXT_WEIGH = 1762237800  # some morning


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class RemindersServerUI(UIBase):
    def test_no_card_on_the_server(self):
        self.page.click("#avatar")
        expect(self.page.locator("#tab-profile")).to_be_visible()
        expect(self.page.locator("#remindCard")).to_be_hidden()


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class RemindersAppUI(UIBase):
    def setUp(self):
        super().setUp()
        self.ctx.close()
        self.open_app({"permission": "ask", "asked": False, "next": {"day": None, "weigh": None}})

    def open_app(self, status):
        self.status = status
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
        expect(self.page.locator("#remindCard")).to_be_visible()

    def android(self, route):
        req = route.request
        path = req.url.split("/api/android/", 1)[1].split("?")[0]
        if path == "reminders":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.status))
        elif path == "action" and req.method == "POST":
            action = json.loads(req.post_data)["action"]
            self.actions.append(action)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "action": action}))
        elif path == "band":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"known": False, "connected": False, "status": "gb_stopped", "busy": None, "last_sync": None}))
        elif path == "backup":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"on": False, "folder": None, "every": "week", "keep": 5, "running": False, "last_ok": None,
                 "last_file": None, "kept": None, "error": None, "error_at": None, "next": None}))
        else:
            route.fulfill(status=404, content_type="application/json", body="{}")

    def from_app(self, status):
        self.status = status
        self.page.evaluate("(s) => window.helioReminders(s)", status)

    def saved(self):
        p = self.state / "settings.json"
        return json.loads(p.read_text()).get("reminders") if p.exists() else None

    def wait_saved(self, check):
        for _ in range(50):
            r = self.saved()
            if r is not None and check(r):
                return r
            self.page.wait_for_timeout(50)
        self.fail(f"not saved: {self.saved()}")

    def wait_action(self, action):
        """The page saved the settings, then told the app: only then does Java answer (the real order)."""
        for _ in range(50):
            if self.actions and self.actions[-1] == action:
                return
            self.page.wait_for_timeout(50)
        self.fail(f"no {action}: {self.actions}")

    def test_off_by_default_and_nothing_asked(self):
        expect(self.page.locator("#remDayOn")).not_to_be_checked()
        expect(self.page.locator("#remWeighOn")).not_to_be_checked()
        expect(self.page.locator("#remDayTime")).to_have_value("21:30")
        expect(self.page.locator("#remWeighTime")).to_have_value("07:30")
        expect(self.page.locator("#remWeighDays button.on")).to_have_count(7)
        # permission not granted, but nothing is on: no warning, nothing asked
        expect(self.page.locator("#remErr")).to_be_hidden()
        expect(self.page.locator("#remAllow")).to_be_hidden()
        self.page.wait_for_timeout(200)
        self.assertEqual(self.actions, [])
        self.assertIsNone(self.saved())

    def test_turn_on_asks_the_app_then_shows_the_next_time(self):
        self.page.fill("#remDayTime", "22:15")
        self.page.dispatch_event("#remDayTime", "change")
        self.wait_saved(lambda r: r["day"]["time"] == "22:15")
        self.page.check("#remDayOn")
        r = self.wait_saved(lambda r: r["day"]["on"])
        self.assertEqual(r["day"], {"on": True, "time": "22:15"})
        self.assertFalse(r["weigh"]["on"])
        self.wait_action("reminders")  # Java asks the permission (only now) and sets the alarm
        self.from_app({"permission": "granted", "asked": True, "next": {"day": NEXT_DAY, "weigh": None}})
        expect(self.page.locator("#remNext")).to_be_visible()
        expect(self.page.locator("#remNext")).to_contain_text(self.Lpart("rem.next_day", 0))
        expect(self.page.locator("#remErr")).to_be_hidden()
        # weigh-in on Monday, Wednesday and Friday only
        self.page.check("#remWeighOn")
        self.wait_saved(lambda r: r["weigh"]["on"])
        for i in (2, 4, 6, 7):
            self.page.locator("#remWeighDays button").nth(i - 1).click()
            self.wait_saved(lambda r, i=i: i not in r["weigh"]["days"])
        self.assertEqual(self.saved()["weigh"]["days"], [1, 3, 5])
        expect(self.page.locator("#remWeighDays button.on")).to_have_count(3)
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"), width)
        self.from_app({"permission": "granted", "asked": False, "next": {"day": NEXT_DAY, "weigh": NEXT_WEIGH}})
        expect(self.page.locator("#remNext")).to_contain_text(self.Lpart("rem.next_weigh", 0))
        if SHOTS:
            self.page.locator("#remindCard").screenshot(path=f"{SHOTS}/reminders-{UI_LANG}.png")

    def test_permission_denied_turns_them_off(self):
        self.page.check("#remWeighOn")
        self.wait_saved(lambda r: r["weigh"]["on"])
        self.wait_action("reminders")
        # Android's dialog answered "Don't allow"
        self.from_app({"permission": "ask", "asked": True, "next": {"day": None, "weigh": NEXT_WEIGH}})
        self.wait_saved(lambda r: not r["weigh"]["on"])
        expect(self.page.locator("#remWeighOn")).not_to_be_checked()
        expect(self.page.locator("#remErr")).to_have_text(self.L("rem.denied"))
        expect(self.page.locator("#remSettings")).to_be_visible()
        expect(self.page.locator("#remNext")).to_be_hidden()
        self.page.click("#remSettings")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.actions[-1], "notif_settings")
        if SHOTS:
            self.page.locator("#remindCard").screenshot(path=f"{SHOTS}/reminders-denied-{UI_LANG}.png")

    def test_on_without_permission_keeps_the_choice_and_says_so(self):
        """An imported backup with reminders on, or notifications turned off in Android's settings: the choice stays,
        the card says why nothing will appear and how to fix it."""
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "settings.json").write_text(json.dumps({"reminders": {
            "day": {"on": True, "time": "21:30"}, "weigh": {"on": False, "time": "07:30", "days": [1]}}}))
        self.ctx.close()
        self.open_app({"permission": "ask", "asked": False, "next": {"day": NEXT_DAY, "weigh": None}})
        expect(self.page.locator("#remDayOn")).to_be_checked()
        expect(self.page.locator("#remErr")).to_have_text(self.L("rem.perm_ask"))
        expect(self.page.locator("#remAllow")).to_be_visible()
        self.page.click("#remAllow")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.actions, ["reminders"])
        self.from_app({"permission": "off", "asked": False, "next": {"day": NEXT_DAY, "weigh": None}})
        expect(self.page.locator("#remErr")).to_have_text(self.L("rem.perm_off"))
        expect(self.page.locator("#remSettings")).to_be_visible()
        expect(self.page.locator("#remAllow")).to_be_hidden()
        self.assertTrue(self.saved()["day"]["on"])  # nothing changed by itself

    def test_weigh_in_needs_a_day(self):
        self.page.check("#remWeighOn")
        self.wait_saved(lambda r: r["weigh"]["on"])
        buttons = self.page.locator("#remWeighDays button")
        for i in range(6):
            buttons.nth(i).click()
            self.wait_saved(lambda r, i=i: (i + 1) not in r["weigh"]["days"])
        buttons.nth(6).click()  # the last day
        expect(self.page.locator("#remErr")).to_have_text(self.L("e.reminder_days"))
        self.assertEqual(self.saved()["weigh"]["days"], [7])
        expect(self.page.locator("#remWeighDays button.on")).to_have_count(1)


if __name__ == "__main__":
    unittest.main()
