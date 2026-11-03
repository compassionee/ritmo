"""Browser tests of the automatic backup card (Profile, only in the Android app). The app is simulated: HELIO_ANDROID
set before the page's scripts, GET /api/android/backup and POST /api/android/action answered here as HelioBackup and
the bridge do, window.helioBackup called as Java calls it. On the server the card does not exist.
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
LAST_OK = 1762160400  # 2025-11-03 09:00 UTC
STATUS = {"on": True, "folder": "Backups", "every": "week", "keep": 5, "running": False, "last_ok": LAST_OK,
          "last_file": "ritmo-backup-20251103-100000.tgz", "kept": 3, "error": None, "error_at": None,
          "next": LAST_OK + 7 * 86400}
OFF = {"on": False, "folder": None, "every": "week", "keep": 5, "running": False, "last_ok": None, "last_file": None,
       "kept": None, "error": None, "error_at": None, "next": None}


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class BackupServerUI(UIBase):
    def test_no_card_on_the_server(self):
        self.page.click("#avatar")
        expect(self.page.locator("#tab-profile")).to_be_visible()
        expect(self.page.locator("#backupCard")).to_be_hidden()


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class BackupAppUI(UIBase):
    def setUp(self):
        super().setUp()
        self.ctx.close()
        self.status = dict(OFF)
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
        expect(self.page.locator("#tab-profile")).to_be_visible()

    def android(self, route):
        req = route.request
        path = req.url.split("/api/android/", 1)[1].split("?")[0]
        if path == "backup":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.status))
        elif path == "action" and req.method == "POST":
            action = json.loads(req.post_data)["action"]
            self.actions.append(action)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "action": action}))
        elif path == "band":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"known": False, "connected": False, "status": "gb_stopped", "busy": None, "last_sync": None}))
        else:
            route.fulfill(status=404, content_type="application/json", body="{}")

    def from_app(self, status):
        """What Java sends after every change (HelioActivity.sendBackupStatus)."""
        self.status = status
        self.page.evaluate("(s) => window.helioBackup(s)", status)

    def test_choose_folder_then_backup_now(self):
        card = self.page.locator("#backupCard")
        expect(card).to_be_visible()
        expect(self.page.locator("#backupState")).to_have_text(self.L("backup.off_state"))
        expect(self.page.locator("#backupNow")).to_be_hidden()
        expect(self.page.locator("#backupOff")).to_be_hidden()
        expect(self.page.locator("#backupEvery")).to_have_value("week")
        self.page.click("#backupFolder")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.actions, ["backup_folder"])
        # the folder chosen in the picker; the first backup at once
        self.from_app({**STATUS, "running": True, "last_ok": None, "last_file": None, "kept": None, "next": None})
        expect(self.page.locator("#backupState")).to_have_text(self.L("backup.running"))
        expect(self.page.locator("#backupNow")).to_be_disabled()
        self.from_app(STATUS)
        expect(self.page.locator("#toast")).to_contain_text(self.L("backup.done"))
        expect(self.page.locator("#backupState")).to_contain_text(self.Lpart("backup.last", 0))
        info = self.page.locator("#backupInfo")
        expect(info).to_contain_text("Backups")
        expect(info).to_contain_text("ritmo-backup-20251103-100000.tgz")
        expect(info).to_contain_text(self.L("backup.last_file", file="x", n=3, keep=5).split("·")[1].strip())
        expect(self.page.locator("#backupFolder")).to_have_text(self.L("backup.change"))
        # back up now: the button waits for the app's answer
        self.page.click("#backupNow")
        expect(self.page.locator("#backupState")).to_have_text(self.L("backup.running"))
        self.assertEqual(self.actions[-1], "backup_now")
        self.from_app({**STATUS, "kept": 4})
        expect(self.page.locator("#backupNow")).to_be_enabled()
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"), width)
        if SHOTS:
            card.screenshot(path=f"{SHOTS}/backup-{UI_LANG}.png")

    def test_frequency_and_stop(self):
        self.from_app(STATUS)
        self.page.select_option("#backupEvery", "month")
        expect(self.page.locator("#backupEvery")).to_have_value("month")
        self.page.select_option("#backupEvery", "day")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.actions, ["backup_month", "backup_day"])
        self.page.click("#backupOff")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.actions[-1], "backup_off")
        self.from_app(OFF)
        expect(self.page.locator("#toast")).to_contain_text(self.L("backup.stopped"))
        expect(self.page.locator("#backupState")).to_have_text(self.L("backup.off_state"))
        expect(self.page.locator("#backupInfo")).to_be_hidden()

    def test_folder_gone_is_explained(self):
        self.from_app({**STATUS, "error": "folder_gone", "error_at": LAST_OK + 8 * 86400})
        err = self.page.locator("#backupErr")
        expect(err).to_be_visible()
        expect(err).to_contain_text(self.Lpart("backup.err.folder_gone", 0))
        # the last good backup is still shown, the "next" line is not (it would not happen)
        expect(self.page.locator("#backupState")).to_contain_text(self.Lpart("backup.last", 0))
        expect(self.page.locator("#backupInfo")).not_to_contain_text(self.Lpart("backup.next", 0))
        expect(self.page.locator("#backupFolder")).to_have_text(self.L("backup.change"))
        for code in ("no_space", "write", "export"):
            self.from_app({**STATUS, "error": code, "error_at": LAST_OK})
            expect(err).to_contain_text(self.Lpart(f"backup.err.{code}", 0))
        # an unknown code from a newer app: a generic message, never a raw key
        self.from_app({**STATUS, "error": "<b>x</b>", "error_at": LAST_OK})
        expect(err).to_contain_text(self.Lpart("backup.err.write", 0))
        self.from_app(STATUS)
        expect(err).to_be_hidden()
        if SHOTS:
            self.from_app({**STATUS, "error": "folder_gone", "error_at": LAST_OK + 8 * 86400})
            self.page.locator("#backupCard").screenshot(path=f"{SHOTS}/backup-error-{UI_LANG}.png")

    def test_language_change_rerenders(self):
        self.from_app(STATUS)
        other = "it" if self.page.evaluate("I18N.lang()") == "en" else "en"
        self.page.select_option("#langSel", other)
        self.page.wait_for_function("(l) => I18N.lang() === l", arg=other)
        expect(self.page.locator("#backupFolder")).to_have_text(self.page.evaluate("I18N.t('backup.change')"))
        expect(self.page.locator("#backupState")).to_contain_text(
            self.page.evaluate("I18N.t('backup.last', {when: ''})").strip(" :"))


if __name__ == "__main__":
    unittest.main()
