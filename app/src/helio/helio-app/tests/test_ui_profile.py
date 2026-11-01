"""Browser tests of the simpler Profile tab and profile sheet: one essential line per card in view, the rest in
"Details"; the cleaning rules are rows opening their explanation and sources; the removed sources are a group that
opens; the explanations of the sheet's fields are in each field's Details. Everything stays reachable.
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
class ProfileUI(UIBase):
    def setUp(self):
        super().setUp()
        self.s = self.h.summary(DAY)
        self.page.click("#avatar")
        expect(self.page.locator("#tab-profile")).to_be_visible()

    def T(self, msg):
        return self.page.evaluate("(m) => I18N.tx(m)", msg)

    def test_cards_keep_one_line_and_details(self):
        p = self.s["profile"]
        short = self.page.locator("#profileShort")
        expect(short).to_contain_text(str(p["hrmax"]))
        self.assertLessEqual(len(short.inner_text()), 90)
        expect(self.page.locator("#profileText")).to_be_hidden()
        self.page.locator('details[data-more="prof_calc"] > summary').click()
        expect(self.page.locator("#profileText")).to_contain_text(str(p["bmr"])[:1])
        expect(self.page.locator('details[data-more="prof_calc"]')).to_contain_text(self.L("ui.profile.calories_basal_metabolic_rate")[:40])
        for key, text in (("prof_lang", "lang.note"), ("prof_weight", "ui.profile.from_day_you_enter"),
                          ("prof_catalog", "cat.intro"), ("prof_export", "ui.profile.file_created_by_ritmo"),
                          ("prof_sources", "ui.profile.every_threshold_comes_from"), ("prof_data", "ui.profile.steps_activities_active_time")):
            d = self.page.locator(f'#tab-profile details[data-more="{key}"]')
            if not d.is_visible():
                continue                                                             # card hidden without its data
            self.assertFalse(d.evaluate("(x) => x.open"), key)
            d.locator("> summary").click()
            expect(d).to_contain_text(self.L(text)[:40])

    def test_cleaning_rules_and_removed_sources(self):
        rules = self.s["quality"]["rules"]
        rows = self.page.locator("#qualityRules details.rule")
        expect(rows).to_have_count(len(rules))
        for i, r in enumerate(rules):
            row = rows.nth(i)
            expect(row.locator("> summary")).to_have_text(self.T(r["title"]))
            self.assertGreaterEqual(row.locator("> summary").bounding_box()["height"], 44)
            row.locator("> summary").click()
            expect(row.locator("p.note")).to_have_text(self.T(r["text"]))
            for s in r["sources"]:
                expect(row).to_contain_text(s["title"])
        removed = self.h.REMOVED_SOURCES
        expect(self.page.locator("#removedSummary")).to_have_text(self.L("src.removed_n", n=len(removed)))
        expect(self.page.locator("#removedList")).to_be_hidden()
        self.page.locator("#removedSummary").click()
        expect(self.page.locator("#removedList li")).to_have_count(len(removed))

    def test_sheet_field_explanations_in_details(self):
        self.page.click("#profOpen")
        expect(self.page.locator("#profSheet")).to_be_visible()
        for key, sel in (("prof_work", "#profWorkNote"), ("prof_sheet_more", "#profNote")):
            note = self.page.locator(sel)
            expect(note).to_be_hidden()
            self.page.locator(f'#profSheet details[data-more="{key}"] > summary').click()
            expect(note).to_be_visible()
            expect(note).not_to_be_empty()
        for key in ("prof_sheet", "prof_bf"):
            d = self.page.locator(f'#profSheet details[data-more="{key}"]')
            d.locator("> summary").click()
            expect(d.locator("p.note")).to_be_visible()

    def test_no_horizontal_scroll_and_touch_targets(self):
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for d in self.page.locator("#tab-profile details.more").all():
                if d.is_visible():
                    self.assertGreaterEqual(d.locator("> summary").bounding_box()["height"], 44)
                    d.locator("> summary").click()
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for d in self.page.locator("#tab-profile details.more").all():
                if d.is_visible() and d.evaluate("(x) => x.open"):
                    d.locator("> summary").click()


if __name__ == "__main__":
    unittest.main()
