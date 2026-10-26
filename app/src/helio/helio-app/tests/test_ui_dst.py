"""Browser tests on the days the clock changes (Chromium with Playwright; skipped where it is not installed).

The times typed in "Add an activity" become instants with the offset of their own day: on 25 October 2026 10:00
is 09:00 UTC (+1), the day before it was 08:00 UTC (+2); in the repeated hour an end before the start is its second
occurrence; a field left as it was keeps the saved instant. The day's charts put their ticks on 00, 06, 12 and 18
of the local clock, 7 hours apart across the change.
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from test_dst import AUTUMN, ONE, SPRING, build, local, utc
from test_helio import TZ, load_app
from test_ui import LOCALE, UI_LANG, sync_playwright

if sync_playwright is not None:
    from playwright.sync_api import expect


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class Autumn(unittest.TestCase):
    DAY = AUTUMN

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        build(d / "gb.db", [(cls.DAY - 3 * ONE, cls.DAY + ONE)])
        cls.state = d / "state"
        cls.now = datetime(cls.DAY.year, cls.DAY.month, cls.DAY.day, 21, 30, tzinfo=TZ)
        cls.h = load_app(d / "gb.db", HELIO_STATE=str(cls.state), HELIO_NOW=str(int(cls.now.timestamp())))
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
        self.h.clear_caches()
        self.ctx = self.browser.new_context(viewport={"width": 412, "height": 915}, timezone_id="Europe/Rome",
                                            locale=LOCALE, service_workers="block")
        self.page = self.ctx.new_page()
        self.page.clock.set_fixed_time(self.now)
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(self.base)
        self.page.wait_for_function("([l]) => typeof I18N !== 'undefined' && I18N.lang() === l "
                                    "&& !I18N.t('day.today').startsWith('⟦')", arg=[UI_LANG])
        expect(self.page.locator("#dayLabel")).to_have_text(self.L("day.today"))
        # the day's data has arrived (the sheet opens only with it)
        self.page.wait_for_function("(d) => typeof S !== 'undefined' && S && S.date === d", arg=self.DAY.isoformat())

    def tearDown(self):
        self.ctx.close()
        self.assertEqual(self.errors, [], "JavaScript errors in the page")

    def L(self, key, **params):
        return self.page.evaluate("([k, p]) => I18N.t(k, p)", [key, params])

    def labels(self):
        f = self.state / "labels.json"
        return sorted((v["start"], v["end"]) for v in json.loads(f.read_text()).values()) if f.exists() else []

    def add(self, a, b):
        self.page.click('button.tabbtn[data-tab="fitness"]')
        self.page.click("#addActivity")
        self.page.locator(".type-btn", has_text=self.L("act.work")).click()
        self.page.fill("#sheetFrom", a)
        self.page.fill("#sheetTo", b)
        self.page.click("#sheetSave")
        expect(self.page.locator("#sheet")).to_be_hidden()

    def manual_card(self):
        return self.page.locator(".card.workout", has_text=self.L("act.work"))

    def test_typed_times_use_the_offset_of_the_day(self):
        self.add("10:00", "11:00")
        self.assertEqual(self.labels(), [(local(self.DAY, 10), local(self.DAY, 11))])
        expect(self.manual_card()).to_contain_text("10:00–11:00")
        # edited and saved without touching the times: the same instants
        self.manual_card().locator("button", has_text=self.L("common.edit")).click()
        expect(self.page.locator("#sheetFrom")).to_have_value("10:00")
        self.page.click("#sheetSave")
        expect(self.page.locator("#sheet")).to_be_hidden()
        self.assertEqual(self.labels(), [(local(self.DAY, 10), local(self.DAY, 11))])

    def test_ending_after_midnight(self):
        self.add("22:00", "06:00")
        self.assertEqual(self.labels(), [(local(self.DAY, 22), local(self.DAY + ONE, 6))])

    def test_day_chart_ticks_on_the_local_clock(self):
        self.page.click('button.tabbtn[data-tab="health"]')
        expect(self.page.locator("#stressChart svg")).to_be_visible()
        # x axis labels (the y axis ones are anchored at the end): text and position
        ticks = self.page.evaluate("""() => [...document.querySelectorAll('#stressChart svg text')]
            .filter((t) => t.getAttribute('text-anchor') !== 'end').map((t) => [t.textContent, Number(t.getAttribute('x'))])""")
        pos = dict(ticks)
        self.assertEqual([t for t, _ in ticks], ["00", "06", "12", "18"])
        hours = (local(self.DAY + ONE) - local(self.DAY)) // 3600
        first = {25: 7, 23: 5}.get(hours, 6)  # from 00 to 06: 7 hours on 25 October, 5 on 28 March
        self.assertAlmostEqual((pos["06"] - pos["00"]) / (pos["12"] - pos["06"]), first / 6, places=2)
        self.assertAlmostEqual(pos["18"] - pos["12"], pos["12"] - pos["06"], places=1)

    def test_the_hour_of_the_change(self):
        # from the first 02:45 to the second 02:15: 30 minutes, not a day
        self.add("02:45", "02:15")
        self.assertEqual(self.labels(), [(local(self.DAY, 2, 45), local(self.DAY, 2, 15, fold=1))])
        self.assertEqual(self.labels()[0][1] - self.labels()[0][0], 30 * 60)


class Spring(Autumn):
    DAY = SPRING

    def test_the_hour_of_the_change(self):
        # 02:30 does not exist on 28 March 2027: it is read as 03:30, after the jump
        self.add("02:30", "04:00")
        self.assertEqual(self.labels(), [(utc(2027, 3, 28, 1, 30), local(self.DAY, 4))])


if __name__ == "__main__":
    unittest.main()
