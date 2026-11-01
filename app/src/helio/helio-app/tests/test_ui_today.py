"""Browser tests of the simpler Today tab: one row per comment, advice in one line, four week rows in view, the rest in
"Details" (closed by default). Every piece of information of the previous layout must still be reachable.
"""
import json
import unittest
from pathlib import Path

from test_helio import DAY
from test_ui import UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

PLAN = json.loads((Path(__file__).resolve().parent / "plan-sample.json").read_text())


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class TodayUI(UIBase):
    def setUp(self):
        super().setUp()
        self.h.save_plan({"plan": PLAN})
        self.h._daycache.clear()
        self.page.reload()
        self.wait_catalog()
        expect(self.page.locator("#workoutList .card").first).to_be_attached()
        self.s = self.h.summary(DAY)

    def T(self, msg):
        return self.page.evaluate("(m) => I18N.tx(m)", msg)

    def test_comments_are_rows_and_their_text_is_in_details(self):
        rows = self.page.locator("#insights .ins-row")
        expect(rows).to_have_count(len(self.s["insights"]))
        self.assertGreater(len(self.s["insights"]), 0)
        more = self.page.locator("#insightsMore")
        self.assertFalse(more.evaluate("(d) => d.open"))
        for i, it in enumerate(self.s["insights"]):
            expect(rows.nth(i)).to_contain_text(self.T(it["title"]))
            expect(rows.nth(i)).to_contain_text(self.L(f"tag.{it['tag']}"))
            if it.get("value"):
                expect(rows.nth(i).locator(".ins-val")).to_have_text(self.T(it["value"]))
            self.assertGreaterEqual(rows.nth(i).bounding_box()["height"], 44)
            expect(more).to_contain_text(self.T(it["text"]))                       # the paragraph, in Details
        expect(self.page.locator("#insights")).not_to_contain_text(self.T(self.s["insights"][0]["text"]))
        sleep = rows.filter(has_text=self.L("tag.sleep"))
        sleep.click()
        expect(self.page.locator("#tab-sleep")).to_be_visible()

    def test_advice_in_one_line_with_why_in_details(self):
        adv = self.s["advice"]
        if not adv:
            self.skipTest("no advice in the test database")
        expect(self.page.locator("#adviceTitle")).to_have_text(self.T(adv["title"]))
        expect(self.page.locator("#adviceText")).to_be_hidden()
        self.page.locator("#planMore > summary").click()
        expect(self.page.locator("#adviceText")).to_have_text(self.T(adv["text"]))
        expect(self.page.locator("#adviceSrc summary")).to_contain_text(str(len(adv["sources"])))

    def test_week_rows_split_between_view_and_details(self):
        expect(self.page.locator("#weekStats .ws-row")).to_have_count(4)
        for key in ("wk.zone", "wk.strength", "wk.steps", "wk.sleep"):
            expect(self.page.locator("#weekStats")).to_contain_text(self.L(key))
        more = self.page.locator("#weekCard details.more")
        self.assertFalse(more.evaluate("(d) => d.open"))
        more.locator("> summary").click()
        self.assertGreaterEqual(self.page.locator("#weekMore .ws-row").count(), 3)
        for key in ("wk.load", "wk.rhr", "wk.hrv"):
            expect(self.page.locator("#weekMore")).to_contain_text(self.L(key))
        expect(more).to_contain_text(self.L("wk.refs"))                              # WHO 150-300 and 2 days, 7 hours
        expect(self.page.locator("#weekSrc summary")).to_be_visible()
        # no citations in the rows in view
        self.assertNotRegex(self.page.locator("#weekStats").inner_text(), r"\b(WHO|OMS|Banister)\b")

    def test_stress_is_labelled_as_manufacturer_data(self):
        card = self.page.locator(".card.stat", has=self.page.locator("#stressValue"))
        expect(card.locator(".pill.vendor")).to_have_text("Zepp")                    # short; the note in Details
        card.locator("details.more > summary").click()
        expect(self.page.locator("#todayStressNote")).to_have_text(self.T(self.s["proprietary"]["stress"]["text"]))

    def test_details_closed_and_no_horizontal_scroll(self):
        for d in self.page.locator("#tab-today details.more").all():
            self.assertFalse(d.evaluate("(x) => x.open"))
            if d.is_visible():
                self.assertGreaterEqual(d.locator("> summary").bounding_box()["height"], 44)
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for d in self.page.locator("#tab-today details.more").all():
                if d.is_visible():
                    d.locator("> summary").click()
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for d in self.page.locator("#tab-today details.more").all():
                if d.is_visible():
                    d.locator("> summary").click()


if __name__ == "__main__":
    unittest.main()
