"""Screenshots of whole tabs for the before/after comparison of the layout work (412 px, the test database, a sample
plan). They run only with HELIO_SHOTS=<folder> (and optionally HELIO_SHOTS_TABS=today,sleep...); otherwise skipped.
"""
import json
import os
import unittest
from pathlib import Path

from test_ui import UI_LANG, UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

SHOTS = os.environ.get("HELIO_SHOTS")
TABS = [x for x in os.environ.get("HELIO_SHOTS_TABS", "today").split(",") if x]
PLAN = json.loads((Path(__file__).resolve().parent / "plan-sample.json").read_text())


@unittest.skipIf(sync_playwright is None or not SHOTS, "screenshots only with HELIO_SHOTS")
class TabShots(UIBase):
    def setUp(self):
        super().setUp()
        self.h.save_plan({"plan": PLAN})
        if "food" in TABS:  # a few foods and diary days, so the Food tab is not empty
            from datetime import timedelta
            from test_helio import DAY
            oats = self.h.save_food({"food": {"name": "Rolled oats", "per100": {"kcal": 372, "protein": 13.5, "carbs": 58.7,
                                                                             "fat": 7.0, "fiber": 10.0, "sat": 1.3, "salt": 0.02}}})["id"]
            pasta = self.h.save_food({"food": {"name": "Pasta", "per100": {"kcal": 359, "protein": 12.5, "carbs": 71.2, "fat": 2.0,
                                                                         "fiber": 3.0, "sat": 0.5, "salt": 0.01}}})["id"]
            for i in range(0, 5):
                d = (DAY - timedelta(days=i)).isoformat()
                self.h.diary_op({"date": d, "op": "add", "meal": "breakfast", "food": oats, "g": 80})
                self.h.diary_op({"date": d, "op": "add", "meal": "lunch", "food": pasta, "g": 120 + 10 * i})
                if i:
                    self.h.diary_op({"date": d, "op": "complete", "complete": True})
        self.h._daycache.clear()
        self.page.reload()
        self.wait_catalog()
        expect(self.page.locator("#workoutList .card").first).to_be_attached()

    def test_tabs(self):
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        tag = os.environ.get("HELIO_SHOTS_TAG", "shot")
        for tab in TABS:
            if not self.page.locator(f"#tab-{tab}").is_visible():
                self.page.click("#avatar" if tab == "profile" else f'button.tabbtn[data-tab="{tab}"]')
            expect(self.page.locator(f"#tab-{tab}")).to_be_visible()
            self.page.set_viewport_size({"width": 412, "height": 915})
            # the fixed bars would land in the middle of a full-page picture
            hide = """(on) => document.querySelectorAll('body *').forEach((e) => {
                if (getComputedStyle(e).position === 'fixed' && !e.closest('.sheet')) e.style.visibility = on ? 'hidden' : ''; })"""
            self.page.evaluate(hide, True)
            self.page.screenshot(path=str(Path(SHOTS) / f"{tab}-{tag}-{UI_LANG}.png"), full_page=True)
            self.page.evaluate(hide, False)


if __name__ == "__main__":
    unittest.main()
