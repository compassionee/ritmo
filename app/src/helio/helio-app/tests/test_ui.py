"""Interface tests in a real browser (Chromium with Playwright), as on the phone.

They run where Playwright is installed (on the development computer); on the server they are skipped.
Each test opens the app on a test database, taps the buttons as a person would and checks
what appears on screen, which requests are sent and what ends up in the labels file.
"""
import json
import re
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from test_helio import DAY, TZ, build_db, hm, load_app

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:  # there is no browser on the server
    sync_playwright = None

NOW = datetime(DAY.year, DAY.month, DAY.day, 21, 30, tzinfo=TZ)
# interface language of the browser: HELIO_UI_LANG=it runs the same tests in Italian
UI_LANG = os.environ.get("HELIO_UI_LANG", "en")
LOCALE = {"en": "en-GB", "it": "it-IT"}[UI_LANG]
CATALOG = json.loads((Path(__file__).resolve().parent.parent / "static" / "i18n" / f"{UI_LANG}.json").read_text())


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class UIBase(unittest.TestCase):
    """Test server, browser and common helpers (no tests here: the classes below inherit them)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        build_db(d / "gb.db")
        cls.state = d / "state"
        cls.h = load_app(d / "gb.db", HELIO_STATE=str(cls.state))
        cls.srv = cls.h.ThreadingHTTPServer(("127.0.0.1", 0), cls.h.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}/"
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:  # no browser
            cls.pw.stop()
            cls.srv.shutdown()
            cls.srv.server_close()
            raise unittest.SkipTest(f"Chromium not available: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def setUp(self):
        shutil.rmtree(self.state, ignore_errors=True)
        self.h._daycache.clear()
        self.ctx = self.browser.new_context(viewport={"width": 412, "height": 915}, timezone_id="Europe/Rome",
                                            locale=LOCALE, color_scheme="dark", service_workers="block")
        self.page = self.ctx.new_page()
        self.page.clock.set_fixed_time(NOW)
        self.errors, self.posts = [], []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        self.page.on("request", lambda r: self.posts.append(json.loads(r.post_data or "{}")) if r.method == "POST" else None)
        self.page.goto(self.base)
        self.wait_catalog()
        expect(self.page.locator("#dayLabel")).to_have_text(self.L("day.today"))
        expect(self.page.locator("#workoutList .card").first).to_be_attached()

    def tearDown(self):
        self.ctx.close()
        self.assertEqual(self.errors, [], "JavaScript errors in the page")

    # ------------------------------------------------------------ helpers
    def wait_catalog(self, page=None):
        """The page loads its catalog asynchronously: wait until the test language is in place."""
        (page or self.page).wait_for_function("([l]) => typeof I18N !== 'undefined' && I18N.lang() === l "
                                              "&& !I18N.t('day.today').startsWith('⟦')", arg=[UI_LANG])

    def L(self, key, page=None, **params):
        """Text of a catalog key in the test language, rendered by the page itself (I18N.t, Intl formats)."""
        return (page or self.page).evaluate("([k, p]) => I18N.t(k, p)", [key, params])

    @staticmethod
    def Lpart(key, i=0):
        """The i-th literal piece of a catalog text (the parts between placeholders), for partial checks."""
        v = CATALOG[key]
        v = v["other"] if isinstance(v, dict) else v
        return [x.strip(" :·.") for x in re.split(r"\{[^}]*\}", v) if x.strip(" :·.")][i]

    def fitness(self):
        self.page.click('button.tabbtn[data-tab="fitness"]')
        expect(self.page.locator("#tab-fitness")).to_be_visible()

    def labels(self):
        f = self.state / "labels.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def card(self, text):
        return self.page.locator(".card.workout", has_text=text)

    def sheet(self):
        return self.page.locator("#sheet")

    def save_btn(self):
        return self.page.locator("#sheetSave")

    def pick_type(self, name):
        self.page.locator(".type-btn", has_text=name).click()

    def set_times(self, a, b):
        self.page.fill("#sheetFrom", a)
        self.page.fill("#sheetTo", b)

    def expect_sheet_closed(self):
        expect(self.sheet()).to_be_hidden()

    def expect_save_ready(self):
        expect(self.save_btn()).to_have_text(self.L("common.save"))
        expect(self.save_btn()).to_be_enabled()



@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class UI(UIBase):
    # ------------------------------------------------------------ navigation
    def test_every_tab_renders_on_a_phone_screen(self):
        for tab, title in (("today", "tab.today"), ("fitness", "tab.fitness"), ("sleep", "tab.sleep"),
                           ("food", "title.food"), ("health", "tab.health")):
            self.page.click(f'button.tabbtn[data-tab="{tab}"]')
            expect(self.page.locator(f"#tab-{tab}")).to_be_visible()
            expect(self.page.locator("#title")).to_have_text(self.L(title))
            overflow = self.page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            self.assertLessEqual(overflow, 0, f"tab {tab} scrolls horizontally")
        self.page.click("#avatar")
        expect(self.page.locator("#tab-profile")).to_be_visible()
        expect(self.page.locator("#qualityRules .rule").first).to_be_visible()
        expect(self.page.locator("#error")).to_be_hidden()

    def test_day_navigation(self):
        expect(self.page.locator("#next")).to_be_disabled()
        self.page.click("#prev")
        expect(self.page.locator("#dayLabel")).to_have_text(self.L("day.yesterday"))
        expect(self.page.locator("#next")).to_be_enabled()
        self.page.click("#next")
        expect(self.page.locator("#dayLabel")).to_have_text(self.L("day.today"))
        expect(self.page.locator("#next")).to_be_disabled()

    def test_workout_detail_opens(self):
        self.fitness()
        first = self.page.locator(".card.workout").first
        first.locator(".w-row").click()
        expect(first.locator(".w-detail")).to_be_visible()
        expect(first.locator(".w-detail")).to_contain_text(self.Lpart("fit.method"))
        expect(first.locator(".w-detail svg").first).to_be_visible()

    # ------------------------------------------------------------ labels
    def test_confirm_suggestion(self):
        self.fitness()
        first = self.page.locator(".card.workout").first
        first.locator("button", has_text=self.L("fit.confirm")).click()
        expect(self.page.locator(".card.workout").first).to_contain_text(self.L("fit.confirmed"))
        expect(self.page.locator("#toast")).to_have_text(self.L("common.saved"))
        self.assertEqual(len(self.labels()), 1)
        self.page.reload()
        self.fitness()
        expect(self.page.locator(".card.workout").first).to_contain_text(self.L("fit.confirmed"))

    def test_edit_and_save_again_does_not_stay_on_saving(self):
        """The reported case: after a save, Edit → Save stayed stuck on "Saving…"."""
        self.fitness()
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("fit.confirm")).click()
        for name, check in (("act.walk", {}), ("act.hike", {}), ("act.strength", {"n": 2})):
            self.page.locator(".card.workout").first.locator("button", has_text=self.L("common.edit")).click()
            expect(self.sheet()).to_be_visible()
            self.expect_save_ready()
            self.pick_type(self.L(name))
            if name == "act.strength":
                self.page.locator("#sheetDays button", has_text=self.L("plan.strength_day_n", n=2)).click()
                self.page.fill("#sheetLoad", "15")
            self.save_btn().click()
            self.expect_sheet_closed()
            expect(self.page.locator(".card.workout").first).to_contain_text(self.L("act.strength_day", **check) if check else self.L(name))
        expect(self.page.locator(".card.workout").first).to_contain_text(self.L("fit.load_kg", kg=15))
        lb = list(self.labels().values())
        self.assertEqual(len(lb), 1)
        self.assertEqual((lb[0]["type"], lb[0]["day"], lb[0]["load_kg"]), ("strength", 2, 15))

    def test_remove_label(self):
        self.fitness()
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("fit.confirm")).click()
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("common.edit")).click()
        self.page.click("#sheetRemove")
        self.expect_sheet_closed()
        expect(self.page.locator(".card.workout").first).to_contain_text(self.Lpart("fit.looks_like"))
        expect(self.page.locator("#toast")).to_have_text(self.L("fit.label_removed"))
        self.assertEqual(self.labels(), {})

    def test_change_type_from_suggestion(self):
        self.fitness()
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("fit.change")).click()
        expect(self.page.locator("#sheetTimeRow")).to_be_hidden()      # fixed times: measured by the band
        expect(self.page.locator("#sheetRemove")).to_be_hidden()        # nothing to remove yet
        self.pick_type(self.L("act.swim"))
        expect(self.page.locator("#sheetDayRow")).to_be_hidden()
        expect(self.page.locator("#sheetLoadRow")).to_be_hidden()
        self.pick_type(self.L("act.strength"))
        expect(self.page.locator("#sheetDayRow")).to_be_visible()
        expect(self.page.locator("#sheetLoadRow")).to_be_visible()
        self.pick_type(self.L("act.swim"))
        self.save_btn().click()
        self.expect_sheet_closed()
        expect(self.page.locator(".card.workout").first).to_contain_text(self.L("act.swim"))

    def label_strength_15kg(self):
        self.fitness()
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("fit.change")).click()
        self.pick_type(self.L("act.strength"))
        self.page.locator("#sheetDays button", has_text=self.L("plan.strength_day_n", n=2)).click()
        self.page.fill("#sheetLoad", "15")
        self.save_btn().click()
        self.expect_sheet_closed()
        expect(self.page.locator(".card.workout").first).to_contain_text(self.L("fit.load_kg", kg=15))

    def test_load_does_not_follow_into_swimming(self):
        """The reported case: from strength with 15 kg to swimming, "added weight 15 kg" stayed."""
        self.label_strength_15kg()
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("common.edit")).click()
        expect(self.page.locator("#sheetLoad")).to_have_value("15")
        self.pick_type(self.L("act.swim"))
        expect(self.page.locator("#sheetLoadRow")).to_be_hidden()
        self.posts.clear()
        self.save_btn().click()
        self.expect_sheet_closed()
        card = self.page.locator(".card.workout").first
        expect(card).to_contain_text(self.L("act.swim"))
        expect(card).not_to_contain_text(self.Lpart("fit.load_kg"))
        self.assertEqual(self.posts[-1]["load_kg"], 0)
        self.assertEqual(next(iter(self.labels().values()))["load_kg"], 0)

    def test_load_kept_when_switching_between_types_that_use_it(self):
        self.label_strength_15kg()
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("common.edit")).click()
        self.pick_type(self.L("act.swim"))
        self.pick_type(self.L("act.walk"))                       # the field comes back with the previous value
        expect(self.page.locator("#sheetLoadRow")).to_be_visible()
        expect(self.page.locator("#sheetLoad")).to_have_value("15")
        self.save_btn().click()
        self.expect_sheet_closed()
        expect(self.page.locator(".card.workout").first).to_contain_text(self.L("fit.load_kg", kg=15))
        self.assertEqual(next(iter(self.labels().values()))["load_kg"], 15)

    def test_old_wrong_label_is_shown_without_load(self):
        w = self.h.summary(DAY)["trainings"][-1]   # the cards show the most recent workout first
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "labels.json").write_text(json.dumps({str(w["start"]): {
            "start": w["start"], "end": w["end"], "type": "swim", "day": None, "load_kg": 15, "features": {}}}))
        self.h._daycache.clear()
        self.page.reload()
        self.fitness()
        card = self.page.locator(".card.workout").first
        expect(card).to_contain_text(self.L("act.swim"))
        expect(card).not_to_contain_text(self.Lpart("fit.load_kg"))

    # ------------------------------------------------------------ manual activities
    def test_add_edit_move_and_remove_work_shift(self):
        self.fitness()
        self.page.click("#addActivity")
        expect(self.page.locator("#sheetTimeRow")).to_be_visible()
        expect(self.page.locator(".type-btn.on")).to_contain_text(self.L("act.other"))  # neutral default
        self.pick_type(self.L("act.work"))
        self.set_times("14:00", "20:00")
        self.save_btn().click()
        self.expect_sheet_closed()
        shift = self.card(self.Lpart("fit.entered_by_you"))
        expect(shift).to_contain_text(self.L("act.work"))
        expect(shift).to_contain_text("14:00–20:00")

        shift.locator("button", has_text=self.L("common.edit")).click()
        self.expect_save_ready()
        expect(self.page.locator("#sheetFrom")).to_have_value("14:00")
        self.set_times("15:00", "21:00")
        self.save_btn().click()
        self.expect_sheet_closed()
        expect(self.card(self.Lpart("fit.entered_by_you"))).to_contain_text("15:00–21:00")
        self.assertEqual([(v["start"], v["end"]) for v in self.labels().values()], [(hm(15, 0), hm(21, 0))])

        # and a second edit right after
        self.card(self.Lpart("fit.entered_by_you")).locator("button", has_text=self.L("common.edit")).click()
        self.expect_save_ready()
        self.set_times("15:00", "19:00")
        self.save_btn().click()
        self.expect_sheet_closed()
        expect(self.card(self.Lpart("fit.entered_by_you"))).to_contain_text("15:00–19:00")

        self.card(self.Lpart("fit.entered_by_you")).locator("button", has_text=self.L("common.edit")).click()
        self.page.click("#sheetRemove")
        self.expect_sheet_closed()
        expect(self.card(self.Lpart("fit.entered_by_you"))).to_have_count(0)
        self.assertEqual(self.labels(), {})

    def test_overnight_shift(self):
        self.fitness()
        self.page.click("#addActivity")
        self.set_times("22:00", "06:00")
        self.save_btn().click()
        self.expect_sheet_closed()
        expect(self.card(self.Lpart("fit.entered_by_you"))).to_contain_text(self.L("fmt.dur_hm", h=8, m="00"))
        v = next(iter(self.labels().values()))
        self.assertEqual(v["end"] - v["start"], 8 * 3600)

    def test_form_checks_before_sending(self):
        self.fitness()
        self.page.click("#addActivity")
        self.set_times("06:00", "19:00")
        self.save_btn().click()
        expect(self.page.locator("#sheetErr")).to_have_text(self.L("v.max_12h"))
        self.page.fill("#sheetFrom", "")
        self.save_btn().click()
        expect(self.page.locator("#sheetErr")).to_have_text(self.L("v.times"))
        self.set_times("09:00", "10:00")
        self.pick_type(self.L("act.strength"))
        self.save_btn().click()
        expect(self.page.locator("#sheetErr")).to_contain_text(self.Lpart("v.strength_day"))
        self.assertEqual(self.posts, [])
        self.expect_save_ready()

    def test_close_without_saving(self):
        self.fitness()
        self.page.click("#addActivity")
        self.page.click("#sheetClose")
        self.expect_sheet_closed()
        self.page.click("#addActivity")
        self.page.mouse.click(200, 40)  # tap on the dark backdrop
        self.expect_sheet_closed()
        self.assertEqual(self.posts, [])
        expect(self.page.locator("body")).not_to_have_class("noscroll")

    # ------------------------------------------------------------ errors
    def open_change(self):
        self.fitness()
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("fit.change")).click()
        self.pick_type(self.L("act.walk"))

    def test_server_error_shows_message_and_retry_works(self):
        self.open_change()
        self.page.route("**/api/label", lambda r: r.fulfill(status=500, content_type="application/json",
                                                             body=json.dumps({"error": "disk full"})))
        self.save_btn().click()
        expect(self.page.locator("#sheetErr")).to_have_text(self.L("err.not_saved", msg="disk full"))
        expect(self.save_btn()).to_have_text(self.L("common.retry"))
        expect(self.save_btn()).to_be_enabled()
        expect(self.sheet()).to_be_visible()
        self.page.unroute("**/api/label")
        self.save_btn().click()
        self.expect_sheet_closed()
        self.assertEqual(len(self.labels()), 1)

    def test_network_down(self):
        self.open_change()
        self.page.route("**/api/label", lambda r: r.abort())
        self.save_btn().click()
        expect(self.page.locator("#sheetErr")).to_contain_text(self.L("err.unreachable"))
        expect(self.save_btn()).to_be_enabled()

    def test_server_not_answering_gives_up(self):
        self.open_change()
        self.page.evaluate("window.HELIO_SAVE_TIMEOUT_MS = 800")
        pending = []
        self.page.route("**/api/label", lambda r: pending.append(r))  # no answer
        t = time.time()
        self.save_btn().click()
        expect(self.page.locator("#sheetErr")).to_contain_text(self.L("err.timeout"), timeout=5000)
        self.assertLess(time.time() - t, 5)
        expect(self.save_btn()).to_have_text(self.L("common.retry"))
        expect(self.save_btn()).to_be_enabled()
        for r in pending:  # closes the pending request
            try:
                r.abort()
            except Exception:
                pass
        self.page.unroute("**/api/label")

    def test_expired_login(self):
        self.open_change()
        self.page.route("**/api/label", lambda r: r.fulfill(status=401, body=""))
        self.save_btn().click()
        expect(self.page.locator("#sheetErr")).to_contain_text(self.L("err.session_expired"))

    def test_double_tap_saves_once(self):
        self.open_change()
        self.page.evaluate("() => { const b = document.getElementById('sheetSave'); b.click(); b.click(); b.click(); }")
        self.expect_sheet_closed()
        self.assertEqual(len(self.posts), 1)

    def test_confirm_error_is_visible_outside_the_sheet(self):
        self.fitness()
        self.page.route("**/api/label", lambda r: r.fulfill(status=500, content_type="application/json",
                                                             body=json.dumps({"error": "disk full"})))
        btn = self.page.locator(".card.workout").first.locator("button", has_text=self.L("fit.confirm"))
        btn.click()
        expect(self.page.locator("#toast")).to_contain_text(self.L("err.not_saved", msg="disk full"))
        expect(self.page.locator("#toast")).to_have_class("toast err")
        expect(self.page.locator(".card.workout").first.locator("button", has_text=self.L("common.retry"))).to_be_enabled()

    def test_data_error_banner(self):
        self.page.route("**/api/summary*", lambda r: r.fulfill(status=500, content_type="application/json",
                                                                body=json.dumps({"error": "database not readable"})))
        self.page.click("#prev")
        expect(self.page.locator("#error")).to_contain_text("database not readable")
        self.page.unroute("**/api/summary*")
        self.page.click("#next")
        expect(self.page.locator("#error")).to_be_hidden()


if __name__ == "__main__":
    unittest.main(verbosity=2)
