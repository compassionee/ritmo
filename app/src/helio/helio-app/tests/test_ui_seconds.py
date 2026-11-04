"""Browser tests of the heart rate of every second and of the band's SDNN (Helio Strap), on synthetic data built on the
confirmed formats (test_seconds.py): the run's details show the recovery at 60 s with its note and sources, the zones say
how many minutes come from seconds, the chart reads the seconds when opened; the night shows SDNN only as a measurement.
Without these tables (every other browser test) nothing of this appears. HELIO_SHOTS=<folder> saves screenshots.
"""
import os
import sqlite3
import threading
import unittest
from pathlib import Path

from test_helio import D0, DAY, build_db, load_app
from test_seconds import GB_TABLES, ROPE, RUN, RUN_END, rope_then_walk, seconds
from test_ui import UI_LANG, UIBase, sync_playwright
import tempfile

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

SHOTS = os.environ.get("HELIO_SHOTS")


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class SecondsUI(UIBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db = Path(cls.tmp.name) / "gb.db"
        with sqlite3.connect(db) as con:
            con.executescript(GB_TABLES)
            for m in range(20):
                seconds(con, RUN + m * 60, [160] * 30 + [170] * 30 if m == 5 else [165] * 60)
            seconds(con, RUN_END, [150] * 55 + [135] * 11 + [120] * 10)
            rope_then_walk(con)
            for t, v in ((D0 + 2 * 3600, 48), (D0 + 4 * 3600, 62), (D0 + 6 * 3600, 55)):
                con.execute("INSERT INTO GENERIC_HRV_SDNN_SAMPLE VALUES (?,1,1,?)", (t * 1000, v))
        cls.h._daycache.clear()

    def setUp(self):
        self.h._daycache.clear()
        self.h._sleepcache.clear()
        super().setUp()

    def test_run_details_recovery_zones_and_seconds_chart(self):
        self.page.click('button.tabbtn[data-tab="fitness"]')
        card = self.page.locator("#workoutList .card.workout").filter(has_text="18:")
        expect(card).to_have_count(1)
        self.requests = []
        self.page.on("request", lambda r: self.requests.append(r.url))
        card.locator(".w-row").click()
        hrr = card.locator(".w-hrr")
        expect(hrr).to_be_visible()
        expect(hrr.locator("b")).to_have_text(self.L("fit.hrr", v=30, a=165, b=135))
        # 21 minutes with a heart rate (from 17:59), the 20 from 18:00 with their seconds
        expect(card).to_contain_text(self.L("fit.zones_sec_of", n=21, m=20))
        # the note says why there is no good or bad, with its sources and their conflicts
        hrr.locator("summary").first.click()
        expect(hrr).to_contain_text(self.L("fit.hrr_note")[:60])
        expect(hrr.locator(".src-fold summary")).to_have_text(self.L("src.fold", n=3))
        self.page.wait_for_function("() => document.querySelectorAll('.w-detail .chart svg path').length > 0")
        self.assertTrue(any("/api/seconds?" in u for u in self.requests))
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"), width)
        if SHOTS:
            card.screenshot(path=f"{SHOTS}/seconds-workout-{UI_LANG}.png")
        # the day's zones card too: the run with the first minute of its recovery (21), the rope session and its
        # walk with the minute after it (34)
        expect(self.page.locator("#hrmaxLabel")).to_contain_text(
            self.L("fit.zones_sec_of", n=self.h.summary(DAY)["hr_minutes"], m=55))

    def test_rope_then_walk_recovery_heart_rate_and_zone_bar(self):
        self.requests = []
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.click('button.tabbtn[data-tab="fitness"]')
        card = self.page.locator("#workoutList .card.workout").filter(has_text="15:57")
        expect(card).to_have_count(1)
        card.locator(".w-row").click()
        stats = card.locator(".w-stats")
        expect(stats).to_contain_text("175")                  # max from the seconds, not 160
        # the chart of the seconds goes to the end of the session (16:29) plus a minute, not only to the recovery
        # after the rope (16:21): asked of the server and drawn
        self.page.wait_for_function("() => document.querySelectorAll('.w-detail .chart svg path').length > 0")
        (url,) = [u for u in self.requests if "/api/seconds?" in u]
        self.assertIn(f"end={ROPE + 1800}", url)
        hrr = card.locator(".w-hrr")
        expect(hrr.locator("b")).to_have_text(self.L("fit.hrr", v=32, a=160, b=128))
        expect(hrr).to_contain_text(self.page.evaluate("(t) => I18N.t('fit.hrr_after_rope', {time: fmtTime(t)})", ROPE + 1200))
        legend = card.locator(".legend")
        expect(legend).to_contain_text(self.L("zone.below"))
        # the bar adds up: the minutes of the legend are the minutes split by seconds
        mins = legend.locator("b").all_inner_texts()
        self.assertEqual(sum(int(x.rstrip("′")) for x in mins), 33)
        expect(card).to_contain_text(self.L("fit.zones_sec", n=33))  # every minute from its seconds: the short text
        if SHOTS:
            card.screenshot(path=f"{SHOTS}/seconds-rope-{UI_LANG}.png")

    def test_hour_label_at_the_end_of_a_workout_chart(self):
        """A workout from 16:07 to 17:05 shows "17" on its axis, inside the chart, also when the chart ends right after
        17:00 (the label sits at the right edge)."""
        z = D0 + 16 * 3600
        for x1 in (z + 3900, z + 3630, z + 3600):
            out = self.page.evaluate("""([x0, x1]) => {
                const b = document.createElement("div"); document.body.append(b);
                const pts = []; for (let t = x0; t <= x1; t += 5) pts.push([t, 120]);
                lineChart(b, pts, x0, x1, { h: 110, gap: 10 });
                const svg = b.querySelector("svg"), W = svg.viewBox.baseVal.width, box = svg.getBoundingClientRect();
                const labels = [...svg.querySelectorAll("text")].filter((n) => /^\d\d$/.test(n.textContent)).map((n) => {
                  const r = n.getBoundingClientRect();
                  return [n.textContent, r.left >= box.left - 0.5 && r.right <= box.right + 0.5];
                });
                b.remove();
                return labels;
            }""", [z + 420, x1])
            self.assertIn(["17", True], out, x1)
            self.assertTrue(all(inside for _, inside in out), out)

    def test_sdnn_is_only_a_measurement(self):
        self.page.click('button.tabbtn[data-tab="sleep"]')
        cardx = self.page.locator("#sdnnCard")
        expect(cardx).to_be_visible()
        expect(self.page.locator("#sdnnValue")).to_have_text("55")
        expect(self.page.locator("#sdnnSub")).to_have_text(self.L("sleep.sdnn_sub", n=3))
        cardx.locator("details.more > summary").click()
        expect(cardx).to_contain_text(self.L("sleep.sdnn_note")[:60])
        expect(cardx.locator(".src-fold summary")).to_have_text(self.L("src.fold", n=2))
        # never judged: no status colour, no comparison
        self.assertEqual(cardx.evaluate("e => e.className"), "card stat")
        if SHOTS:
            cardx.screenshot(path=f"{SHOTS}/seconds-sdnn-{UI_LANG}.png")


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class WithoutSecondsUI(UIBase):
    def test_nothing_new_without_the_tables(self):
        self.page.click('button.tabbtn[data-tab="sleep"]')
        expect(self.page.locator("#sdnnCard")).to_be_hidden()
        self.page.click('button.tabbtn[data-tab="fitness"]')
        card = self.page.locator("#workoutList .card.workout").first
        card.locator(".w-row").click()
        expect(card.locator(".w-detail")).to_be_visible()
        expect(card.locator(".w-hrr")).to_have_count(0)
        expect(self.page.locator("#hrmaxLabel")).not_to_contain_text(self.L("fit.zones_sec", n=1)[:10])


if __name__ == "__main__":
    unittest.main()
