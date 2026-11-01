"""Browser tests of the simpler Fitness tab: a workout card shows icon, type, time, duration, load and "Looks like:"
with Confirm/Change; how sure and why go in its "Details". Load, PAI, zones, VO2max and strength keep their numbers in
view and their explanations, references and sources in Details. Every piece of information is still reachable.
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
class FitnessUI(UIBase):
    def setUp(self):
        super().setUp()
        self.s = self.h.summary(DAY)
        self.fitness()

    def T(self, msg):
        return self.page.evaluate("(m) => I18N.tx(m)", msg)

    def visible_text(self):
        return self.page.evaluate("""() => { const c = document.querySelector('#tab-fitness').cloneNode(true);
            c.querySelectorAll('details.more').forEach((d) => d.querySelectorAll(':scope > :not(summary)').forEach((x) => x.remove()));
            c.querySelectorAll('.w-detail').forEach((x) => x.remove());
            return c.innerText; }""")

    def test_workout_card_reason_in_details(self):
        todo = [w for w in self.s["trainings"] if not w.get("label")]
        self.assertTrue(todo)
        for w in todo:
            card = self.card(self.T(w["name"]))
            expect(card.locator(".w-sug-text b")).to_have_text(self.L("fit.looks_like", name=self.T(w["suggestion"]["name"])))
            expect(card.locator(".w-btns button")).to_have_count(2)                   # Confirm and Change in view
            why = card.locator(".w-why")
            expect(why).to_be_hidden()
            card.locator("details.more > summary").click()
            expect(why).to_contain_text(self.T(w["suggestion"]["reason"]))
            conf = {"low": "conf.low", "medium": "conf.medium", "high": "conf.high"}[w["suggestion"]["confidence"]]
            expect(why).to_contain_text(self.L(conf))
        # the load and the time stay in view
        expect(self.page.locator(".card.workout .w-load b").first).to_be_visible()

    def test_explanations_and_sources_in_details(self):
        checks = [("fit_load", ["load_note_text", "ui.fitness.banister_trimp_28_days"]), ("fit_pai", ["pai_text", "ui.fitness.licensed_algorithm"]),
                  ("fit_zones", ["fit.zone_who"]), ("fit_vo2", []), ("fit_strength", [])]
        for key, texts in checks:
            d = self.page.locator(f'#tab-fitness details[data-more="{key}"]')
            self.assertFalse(d.evaluate("(x) => x.open"), key)
            d.locator("> summary").click()
            for k in texts:
                text = (self.T(self.s["load_note"]["text"]) if k == "load_note_text"
                        else self.T(self.s["proprietary"]["pai"]["text"]) if k == "pai_text" else self.L(k))
                expect(d).to_contain_text(text[:60])
        expect(self.page.locator("#paiTag")).to_have_text("Zepp")
        self.page.locator("#paiSrc summary").click()
        expect(self.page.locator("#paiSrc .coi.warn")).to_have_count(1)             # PAI: flagged, still reachable
        expect(self.page.locator("#hrmaxLabel")).to_contain_text(str(self.s["profile"]["hrmax"]))

    def test_no_citations_in_view_details_closed_no_horizontal_scroll(self):
        text = self.visible_text()
        self.assertNotRegex(text, r"\b(19|20)\d\d\b|\b(WHO|OMS|Banister)\b")
        self.assertNotRegex(text, re.escape(self.L("src.coi_badge")))
        details = self.page.locator("#tab-fitness details.more")
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
