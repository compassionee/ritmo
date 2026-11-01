"""Browser tests of the simpler Health tab: short titles, value, status and bands in view; explanations and sources
in each card's "Details"; the readiness reasons as chips; Zepp's stress as a short pill with its note in Details.
Every piece of information of the previous layout must still be reachable.
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
class HealthUI(UIBase):
    def setUp(self):
        super().setUp()
        self.s, self.tr = self.h.summary(DAY), self.h.trends(DAY)
        self.page.click('button.tabbtn[data-tab="health"]')
        expect(self.page.locator("#tab-health")).to_be_visible()

    def T(self, msg):
        return self.page.evaluate("(m) => I18N.tx(m)", msg)

    def visible_text(self):
        return self.page.evaluate("""() => { const c = document.querySelector('#tab-health').cloneNode(true);
            c.querySelectorAll('details.more').forEach((d) => d.querySelectorAll(':scope > :not(summary)').forEach((x) => x.remove()));
            return c.innerText; }""")

    def check_explained(self, box, it):
        """Explanation, sources and conflict flags of an item, inside its Details."""
        expect(box.locator(".h-text")).to_have_text(self.T(it["text"]))
        box.locator("details.src-fold > summary").click()
        for s in it["sources"]:
            expect(box).to_contain_text(s["title"])
        n = sum(1 for s in it["sources"] if s["coi"] == "present")
        expect(box.locator(".coi.warn")).to_have_count(n)
        return n

    def test_items_collecting_are_compact_rows_in_one_card(self):
        items = self.tr["health"]["items"]
        self.assertTrue(all(it["status"] == "collecting" for it in items))           # test database: 1 day of data
        expect(self.page.locator("#healthItems .hcard")).to_have_count(1)
        card = self.page.locator("#healthItems .hcard.waiting")
        expect(card.locator("h4")).to_have_text(self.L("health.waiting_title"))
        rows = card.locator("details.h-wait")
        expect(rows).to_have_count(len(items))
        coi = 0
        for i, it in enumerate(items):
            row = rows.nth(i)
            expect(row.locator("> summary b")).to_have_text(self.T(it["title"]))
            expect(row.locator("> summary .pill")).to_have_text(f"{it['days']}/{it['need']}")
            self.assertGreaterEqual(row.locator("> summary").bounding_box()["height"], 44)
            self.assertFalse(row.evaluate("(d) => d.open"))
            row.locator("> summary").click()
            expect(row).to_contain_text(self.T(it["label"]))                         # "3 days of data needed (1/3)"
            expect(row).to_contain_text(self.T(it["period"]))
            expect(row.locator(".bandbar")).to_be_visible()
            coi += self.check_explained(row, it)
        self.assertGreater(coi, 0)                                                   # conflict flags still reachable

    def test_items_with_data_are_full_cards(self):
        h, orig = self.h, self.h.health

        def ready(con, d):  # two items with data, the others still collecting
            rep = orig(con, d)
            for it, v in zip(rep["items"][:2], (9000, 160)):
                b = h.band_of(v, [(x["from"], x["to"], None, x["level"]) for x in it["bands"]])
                it.update(value=v, status="ok", level=b[3], label={"$": "h.band.good"}, display=None)
            return rep
        h.health = ready
        try:
            h._daycache.clear()
            self.page.reload()
            self.wait_catalog()
            self.page.click('button.tabbtn[data-tab="health"]')
            full = self.page.locator("#healthItems .hcard:not(.waiting)")
            expect(full).to_have_count(2)
            for i in range(2):
                c = full.nth(i)
                expect(c.locator(".bandbar .marker")).to_have_count(1)
                expect(c.locator(".h-value .pill")).to_be_visible()
                expect(c.locator(".h-text")).to_be_hidden()
                c.locator("details.more > summary").click()
                expect(c.locator(".h-text")).not_to_be_empty()
            expect(self.page.locator("#healthItems .hcard.waiting details.h-wait")).to_have_count(len(self.tr["health"]["items"]) - 2)
            # with ratings the summary card is back (with its note) and "Collecting" has no subtitle
            expect(self.page.locator("#healthSummaryCard")).to_be_visible()
            expect(self.page.locator("#healthItems .hcard.waiting .h-sub")).to_have_count(0)
        finally:
            h.health = orig

    def test_vitals_readiness_summary_and_stress(self):
        expect(self.page.locator("#vitChart svg")).to_be_visible()                   # chart and chips stay in view
        expect(self.page.locator("#vitChips button")).to_have_count(5)
        expect(self.page.locator("#vitText")).to_be_hidden()
        self.page.locator(".vitals-card details.more > summary").click()
        expect(self.page.locator("#vitText")).not_to_be_empty()
        r = self.s["readiness"]
        if r:
            expect(self.page.locator("#readyParts .reason")).to_have_count(len(r["parts"]))
        expect(self.page.locator("#readyCard .pill")).to_have_text(self.L("common.estimate"))
        # nothing rated yet: one card "Collecting" with the sentence as its subtitle and the note on the ratings
        expect(self.page.locator("#healthSummaryCard")).to_be_hidden()
        waiting = self.page.locator("#healthItems .hcard.waiting")
        expect(waiting.locator(".h-sub")).to_have_text(self.L("health.collecting"))
        waiting.locator('details[data-more="health_summary"] > summary').click()
        expect(waiting).to_contain_text(self.L("ui.health.ratings_compare_values_thresholds"))
        if self.s.get("stress"):
            expect(self.page.locator("#stressCard .row .pill")).to_have_text("Zepp")
            self.page.locator("#stressCard details.more > summary").click()
            expect(self.page.locator("#stressCard details.more")).to_contain_text(self.L("ui.health.zepp_algorithm"))
            expect(self.page.locator("#stressNote")).to_have_text(self.T(self.s["proprietary"]["stress"]["text"]))

    def test_no_citations_in_view_details_closed_no_horizontal_scroll(self):
        text = self.visible_text()
        self.assertNotRegex(text, r"\b(19|20)\d\d\b")                               # "Author 2022" only in Details
        self.assertNotRegex(text, re.escape(self.L("src.coi_badge")))
        details = self.page.locator("#tab-health details.more")
        self.assertGreater(details.count(), 5)
        for d in details.all():
            self.assertFalse(d.evaluate("(x) => x.open"))
            if d.is_visible():
                self.assertGreaterEqual(d.locator("> summary").bounding_box()["height"], 44)
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for d in details.all():
                if d.is_visible():
                    d.locator("> summary").click()
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for d in details.all():
                if d.is_visible():
                    d.locator("> summary").click()


if __name__ == "__main__":
    unittest.main()
