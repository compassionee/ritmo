"""Browser tests of the simpler Sleep tab: the hypnogram stays in view; the night's paragraph, the note on Zepp's score
and the final note are in Details; every key point shows name, value and status, with reference and sources in the
row's own Details; the regularity explanation is in Details. Every piece of information is still reachable.
"""
import re
import unittest

from test_helio import DAY
from test_ui import UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class SleepUI(UIBase):
    def setUp(self):
        super().setUp()
        self.s = self.h.summary(DAY)
        self.page.click('button.tabbtn[data-tab="sleep"]')
        expect(self.page.locator("#tab-sleep")).to_be_visible()

    def T(self, msg):
        return self.page.evaluate("(m) => I18N.tx(m)", msg)

    def visible_text(self):
        return self.page.evaluate("""() => { const c = document.querySelector('#tab-sleep').cloneNode(true);
            c.querySelectorAll('details.more').forEach((d) => d.querySelectorAll(':scope > :not(summary)').forEach((x) => x.remove()));
            return c.innerText; }""")

    def test_night_card(self):
        expect(self.page.locator("#sleepTitle")).to_have_text(self.T(self.s["sleep_insight"]["title"]))
        expect(self.page.locator("#stageChart svg")).to_be_visible()                 # the hypnogram stays in view
        expect(self.page.locator("#sleepScore")).to_be_visible()                     # manufacturer's score: a pill
        expect(self.page.locator("#sleepText")).to_be_hidden()
        self.page.locator(".sleep-card details.more > summary").click()
        expect(self.page.locator("#sleepText")).to_have_text(self.T(self.s["sleep_insight"]["text"]))
        expect(self.page.locator("#sleepScoreNote")).to_contain_text(self.T(self.s["proprietary"]["sleep_score"]["text"])[:40])
        expect(self.page.locator(".sleep-card details.more")).to_contain_text(self.L("ui.sleep.stages_times_score_are"))
        expect(self.page.locator("#sleepScoreSrc summary")).to_be_visible()

    def test_key_points_are_rows_with_their_own_details(self):
        hl = self.s["sleep_highlights"]
        rows = self.page.locator("#sleepHighlights details.hl-row")
        expect(rows).to_have_count(len(hl))
        coi = 0
        for i, x in enumerate(hl):
            row = rows.nth(i)
            summary = row.locator("> summary")
            for text in (self.T(x["title"]), self.T(x["display"]), self.T(x["label"])):
                expect(summary).to_contain_text(text)
            self.assertGreaterEqual(summary.bounding_box()["height"], 44)
            self.assertFalse(row.evaluate("(d) => d.open"))
            summary.click()
            expect(row).to_contain_text(self.T(x["ref"]))
            for s in x["sources"]:
                expect(row).to_contain_text(s["title"])
            coi += sum(1 for s in x["sources"] if s["coi"] == "present")
            expect(row.locator(".coi.warn")).to_have_count(sum(1 for s in x["sources"] if s["coi"] == "present"))
        self.assertGreater(coi, 0)                                                   # the conflict flags are all still there

    def test_wrist_temperature_with_its_unit(self):
        v = self.s["sleep"]["temp"]
        expect(self.page.locator("#sleepTemp")).to_have_text(f"{self.page.evaluate('(v) => I18N.num(v, 1)', v)}\u00a0°C")

    def test_regularity_explanation_in_details(self):
        sri = self.s["sri"]
        expect(self.page.locator("#sriText")).to_be_hidden()
        self.page.locator("#sriCard details.more > summary").click()
        expect(self.page.locator("#sriText")).to_have_text(self.T(sri["text"]))
        expect(self.page.locator("#sriSrc summary")).to_be_visible()

    def test_no_citations_in_view_details_closed_no_horizontal_scroll(self):
        self.assertNotRegex(self.visible_text(), r"\b(19|20)\d\d\b")                # "Author 2017" only in Details
        self.assertNotRegex(self.visible_text(), re.escape(self.L("src.coi_badge")))
        for d in self.page.locator("#tab-sleep details.more").all():
            self.assertFalse(d.evaluate("(x) => x.open"))
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            # (those of a hidden card, such as SDNN without band measurements, are skipped)
            shown = [d for d in self.page.locator("#tab-sleep details.more").all() if d.is_visible()]
            for d in shown:
                d.locator("> summary").click()
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for d in shown:
                d.locator("> summary").click()


if __name__ == "__main__":
    unittest.main()
