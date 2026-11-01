"""Browser tests of the simpler Food tab: the "mark the day as complete" hint only until first used, then behind an (i)
icon (saved in the settings); every nutrient check is a row (name, value, status) opening its reference, explanation
and sources; weight check, athletes, recovery and method keep one line in view and the rest in Details.
"""
import json
import re
import unittest
import urllib.request
from datetime import timedelta

from test_helio import DAY
from test_ui import UIBase, sync_playwright
from test_ui_charts import OUTSIDE

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

OATS = {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "sat": 1.3, "salt": 0.02}


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class FoodLayoutUI(UIBase):
    def api(self, path, body):
        req = urllib.request.Request(self.base.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Helio": "1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def open_food(self):
        self.h._daycache.clear()
        self.page.reload()
        self.wait_catalog()
        self.page.click('button.tabbtn[data-tab="food"]')
        expect(self.page.locator("#tab-food")).to_be_visible()

    def seed(self, days=4):
        oats = self.api("/api/food", {"food": {"name": "Oats", "per100": OATS}})["id"]
        for i in range(days):
            d = (DAY - timedelta(days=i)).isoformat()
            self.api("/api/diary", {"date": d, "op": "add", "meal": "lunch", "food": oats, "g": 100})
            if i:
                self.api("/api/diary", {"date": d, "op": "complete", "complete": True})

    def visible_text(self):
        return self.page.evaluate("""() => { const c = document.querySelector('#tab-food').cloneNode(true);
            c.querySelectorAll('details.more').forEach((d) => d.querySelectorAll(':scope > :not(summary)').forEach((x) => x.remove()));
            c.querySelectorAll('[hidden]').forEach((x) => x.remove());
            return c.innerText; }""")

    def test_complete_day_hint_only_the_first_time(self):
        self.seed(1)
        self.open_food()
        note, info = self.page.locator("#nutCompleteNote"), self.page.locator("#nutCompleteInfo")
        expect(note).to_be_visible()                                                 # first time: the hint is there
        expect(info).to_be_hidden()
        self.page.click("#nutComplete")
        expect(self.page.locator("#nutComplete")).to_have_text(self.L("nut.day_complete"))
        expect(info).to_be_visible()                                                 # used once: behind the (i) icon
        expect(note).to_be_hidden()
        self.assertGreaterEqual(info.bounding_box()["height"], 44)
        self.assertEqual(json.loads((self.state / "settings.json").read_text())["hints"], ["day_complete"])
        info.click()
        expect(note).to_be_visible()
        expect(info).to_have_attribute("aria-expanded", "true")
        info.click()
        expect(note).to_be_hidden()
        self.open_food()                                                             # kept in the settings
        expect(info).to_be_visible()
        expect(note).to_be_hidden()

    def test_checks_are_rows_with_their_details(self):
        self.seed()
        self.open_food()
        rows = self.page.locator("#nutWeekChecks .chk")
        expect(rows.nth(3)).to_be_visible()                                          # wait for the drawing, then count
        self.assertGreater(rows.count(), 3)
        coi = 0
        for r in rows.all():
            d = r.locator("details.chk-row")
            self.assertFalse(d.evaluate("(x) => x.open"))
            self.assertGreaterEqual(d.locator("> summary").bounding_box()["height"], 44)
            expect(d.locator("> summary .pill")).to_be_visible()
            expect(d.locator(".chk-target")).to_be_hidden()
            d.locator("> summary").click()
            expect(d.locator(".chk-target")).to_be_visible()
            expect(d.locator("> p")).not_to_be_empty()
            d.locator("details.src-fold > summary").click()
            coi += d.locator(".coi.warn").count()
        self.assertGreater(coi, 0)                                                   # protein references: flagged, reachable
        # the day in progress keeps its progress bars in view
        expect(self.page.locator("#nutDayChecks .chk-bar").first).to_be_visible()
        # same layout in every row at 360 px: name and value on one line, the pill below, nothing wider than the row
        self.page.set_viewport_size({"width": 360, "height": 800})
        for r in rows.all():
            s = r.locator("details.chk-row > summary")
            name, val, pill = (s.locator(x).bounding_box() for x in ("b", ".chk-val", ".pill"))
            self.assertAlmostEqual(name["y"] + name["height"] / 2, val["y"] + val["height"] / 2, delta=4)
            self.assertGreater(pill["y"], name["y"] + name["height"] - 2)
            self.assertLessEqual(pill["x"] + pill["width"], s.bounding_box()["x"] + s.bounding_box()["width"] + 0.5)
        self.assertNotIn(self.L("nut.st.certain"), self.page.locator("#nutWeekChecks").inner_text())

    def test_weight_check_one_line_and_details(self):
        self.seed()
        self.open_food()
        # same pattern as the composition card: "Collecting: 0 of 4 weigh-ins · 0 of 21 complete days"
        expect(self.page.locator("#nutWeightShort")).to_have_text(re.compile(r"\d+ \S+ 4 .*·.*\d+ \S+ 21 "))
        expect(self.page.locator("#nutWeightText")).to_be_hidden()
        self.page.locator('details[data-more="nut_weight"] > summary').click()
        expect(self.page.locator("#nutWeightText")).to_contain_text(self.Lpart("wc.need"))
        for key in ("nut_athlete", "nut_method"):
            d = self.page.locator(f'details[data-more="{key}"]')
            self.assertFalse(d.evaluate("(x) => x.open"))
            d.locator("> summary").click()
            expect(d.locator("p.note")).not_to_be_empty()

    def test_composition_collecting_line_and_food_buttons(self):
        self.seed()
        self.open_food()
        expect(self.page.locator("#compEmpty")).to_have_text(self.L("comp.short.need", weighins=4, span=21, now_w=0, now_span=0))
        # "+ Food / + Recipe / + Saved meal": one row, the size of the meals' "Add" buttons, also at 360 px
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            add = self.page.locator("#nutMeals .add-btn.small").first.bounding_box()
            add_font = self.page.locator("#nutMeals .add-btn.small").first.evaluate("(b) => getComputedStyle(b).fontSize")
            boxes = [self.page.locator(f"#{i}").bounding_box() for i in ("newFood", "newRecipe", "newMeal")]
            self.assertEqual(len({round(b["y"]) for b in boxes}), 1, width)              # one row
            for i, b in zip(("newFood", "newRecipe", "newMeal"), boxes):
                self.assertAlmostEqual(b["height"], add["height"], delta=2)
                label = self.page.locator(f"#{i} span").nth(1)
                self.assertEqual(label.evaluate("(x) => getComputedStyle(x).fontSize"), add_font)

    def test_no_citations_in_view_no_horizontal_scroll(self):
        self.seed()
        self.open_food()
        expect(self.page.locator("#nutWeekChecks .chk").first).to_be_visible()
        text = self.visible_text()
        self.assertNotRegex(text, r"\b(EFSA|WHO|OMS)\s+(19|20)\d\d\b|\b[A-Z][a-z]+ (et al\.|e|and) [A-Z][a-z]+ (19|20)\d\d\b")
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            self.assertEqual(self.page.evaluate(OUTSIDE), [], width)
            for d in self.page.locator("#tab-food details.more").all():
                if d.is_visible():
                    d.locator("> summary").click()
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for d in self.page.locator("#tab-food details.more").all():
                if d.is_visible() and d.evaluate("(x) => x.open"):
                    d.locator("> summary").click()


if __name__ == "__main__":
    unittest.main()
