"""Browser tests of the cumulative balance and composition cards (Food tab), at phone width, with synthetic data.

Details are closed by default and hold everything else; every piece of information stays reachable there.
The band history is synthetic (as in test_balance.py): 28 days with a known expenditure, two days with the band worn less
than 20 hours and strength sessions twice a week; diary, weigh-ins and profile go through the real API.
HELIO_SHOTS=<folder> also saves a screenshot of each card.
"""
import json
import os
import unittest
import urllib.request
from datetime import timedelta
from pathlib import Path

from test_helio import DAY
from test_nutrition import hist_day
from test_ui import UI_LANG, UIBase, sync_playwright
from test_ui_charts import OUTSIDE

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

FOOD = {"kcal": 160, "protein": 16, "carbs": 15, "fat": 3.9, "fiber": 2}
STRENGTH = {"label": {"type": "strength"}, "duration_min": 50, "start": 0, "end": 3000, "name": "Strength"}
SHOTS = os.environ.get("HELIO_SHOTS")


def synthetic_history(d, n):
    """Band days up to d: 2,650 kcal (2,550-2,750), strength on Mondays and Thursdays, two short wear days."""
    out = []
    for i in range(n - 1, -1, -1):
        x = d - timedelta(days=i)
        worn = 1100 if (DAY - x).days in (5, 12) else 1440
        out.append(hist_day(x, total=2650, rng=(2550, 2750), worn=worn,
                            trainings=[STRENGTH] if x.weekday() in (0, 3) else ()))
    return out


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class BalanceUI(UIBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        h = cls.h

        def payload(d, period):  # same as balance_payload, with the synthetic band history
            today = h.now().date()
            d = min(d, today)
            ns = h.nutrition_settings()
            since = h.date.fromisoformat(ns["balance_from"]) if ns["balance_from"] else None
            rng = h.balance_period(period, d, since)
            hist = synthetic_history(rng[1], (rng[1] - rng[0]).days + 1) if rng else []
            p = {"weight": 83.0, "height": 178, "age": 40, "sex": "M", "missing": []}
            return h.balance_analysis(d, period, hist, h.diary_days(*rng) if rng else {}, h.weights_load(), p,
                                      ns["goal"], today, since, h.profile_load())
        cls._orig = h.balance_payload
        h.balance_payload = payload

    @classmethod
    def tearDownClass(cls):
        cls.h.balance_payload = cls._orig
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.page.set_viewport_size({"width": 412, "height": 915})

    def api(self, path, body):
        req = urllib.request.Request(self.base.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Helio": "1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def seed(self, weights=True):
        fid = self.api("/api/food", {"food": {"name": "Synthetic meal", "per100": FOOD}})["id"]
        for i in range(1, 28):
            day = (DAY - timedelta(days=i)).isoformat()
            self.api("/api/diary", {"date": day, "op": "add", "meal": "lunch", "food": fid, "g": 1300 + 10 * (i % 5)})
            if i not in (9, 17, 23):  # three days left open
                self.api("/api/diary", {"date": day, "op": "complete", "complete": True})
        if weights:
            for i, kg in enumerate((84.1, 83.9, 83.8, 83.4, 83.3, 83.0, 82.8, 82.6)):
                self.api("/api/weight", {"date": (DAY - timedelta(days=27 - 3 * i - (i > 4))).isoformat(), "kg": kg})

    def open_food(self):
        self.h._daycache.clear()
        self.page.reload()
        self.wait_catalog()
        self.page.click('button.tabbtn[data-tab="food"]')
        expect(self.page.locator("#cumCard")).to_be_visible()

    def shot(self, sel, name):
        if SHOTS:  # a tall window: the fixed bars do not cover the card
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            self.page.set_viewport_size({"width": 412, "height": 3200})
            hide = """(on) => document.querySelectorAll('body *').forEach((e) => {
                if (getComputedStyle(e).position === 'fixed' && !e.closest('.sheet')) e.style.visibility = on ? 'hidden' : ''; })"""
            self.page.evaluate(hide, True)
            self.page.locator(sel).screenshot(path=str(Path(SHOTS) / f"{name}-{UI_LANG}.png"))
            self.page.evaluate(hide, False)

    def visible_text(self, card):
        """Text of the card outside its Details."""
        return self.page.evaluate("""(sel) => { const c = document.querySelector(sel).cloneNode(true);
            c.querySelectorAll("details.more").forEach((d) => d.remove()); return c.innerText; }""", card)

    def details(self, card):
        d = self.page.locator(f"{card} details.more")
        if not d.evaluate("(x) => x.open"):
            d.locator("> summary").click()
        return d

    def test_cards_with_synthetic_data(self):
        self.seed()
        self.open_food()
        expect(self.page.locator("#cumSwitch button.on")).to_have_text(self.L("cum.p.28d"))
        # in view: total with the sign, margin, days included and average, chart, fat at most
        expect(self.page.locator("#cumBody")).to_be_visible()
        expect(self.page.locator("#cumDays")).to_contain_text(self.L("cum.days", n=22, days=27, mean="−").split("−")[0])
        expect(self.page.locator("#cumSign")).to_have_text(self.L("cum.deficit"))
        expect(self.page.locator("#cumUnsure")).to_be_visible()
        expect(self.page.locator("#cumPartialPill")).to_be_hidden()
        expect(self.page.locator("#cumChart svg path.cum-band")).to_have_count(1)
        expect(self.page.locator("#cumChart svg circle.cum-out")).to_have_count(5)    # 3 open diaries, 2 short wear days
        expect(self.page.locator("#cumFat")).to_contain_text(self.Lpart("cum.fat_line", 0))
        # composition: three boxes, lean share, energy pill, three chips with a status, one judgement
        expect(self.page.locator("#compBody")).to_be_visible()
        expect(self.page.locator("#compCard > .row .pill, #compCard > .pill")).to_have_count(0)   # the title says "estimate"
        expect(self.page.locator("#compFat")).to_contain_text("−")
        expect(self.page.locator("#compLean")).to_contain_text("−")
        expect(self.page.locator("#compEnergyPill")).to_be_visible()
        expect(self.page.locator("#compChips .chip-st")).to_have_count(3)
        expect(self.page.locator("#compChips .chip-st.ok")).to_have_count(2)            # protein and strength
        expect(self.page.locator("#compChips .chip-st.warn")).to_have_count(1)          # deficit above 500 kcal
        expect(self.page.locator("#compChips .chip-st.warn .st")).to_have_attribute("aria-label", self.L("comp.warn"))
        # deficit above 500 kcal a day: the judgement adds that the benefit of strength training shrinks
        expect(self.page.locator("#compJudge")).to_have_text(self.L("bc.judge_over", base={"$": "bc.you.both_good"}, max=500))
        # kg always with two decimals, thousands always grouped (also in Italian)
        for sel in ("#compWeight", "#compFat", "#compLean", "#compWeightRange", "#compFatRange", "#compLeanRange"):
            for num in __import__("re").findall(r"\d+[.,]?\d*", self.page.locator(sel).inner_text()):
                self.assertRegex(num, r"^\d+[.,]\d\d$", (sel, num))
        for sel in ("#cumTotal", "#cumRange", "#cumDays"):
            self.assertNotRegex(self.page.locator(sel).inner_text(), r"\d{4}", sel)
        # Details: closed by default, nothing of them in view, no citations in view
        for card in ("#cumCard", "#compCard"):
            self.assertFalse(self.page.locator(f"{card} details.more").evaluate("(x) => x.open"), card)
            expect(self.page.locator(f"{card} details.more > summary")).to_have_text(self.L("more.title"))
            self.assertGreaterEqual(self.page.locator(f"{card} details.more > summary").bounding_box()["height"], 44)
            self.assertNotRegex(self.visible_text(card), r"\b(19|20)\d\d\b", card)   # "Author 2012" only in Details
        expect(self.page.locator("#compTrend")).to_be_hidden()
        self.shot("#cumCard", "cum")
        self.shot("#compCard", "comp")
        self.page.set_viewport_size({"width": 412, "height": 915})
        # every piece of information is still there, inside Details
        self.details("#cumCard")
        expect(self.page.locator("#cumExcluded")).to_have_text(" ".join([self.L("cum.excluded", diary=3, band=2), self.L("cum.today")]))
        expect(self.page.locator("#cumFatNote")).to_be_visible()
        expect(self.page.locator("#cumChoice")).to_have_text(self.L("cum.choice", pct=70))
        expect(self.page.locator("#cumCard details.more")).to_contain_text(self.L("cum.how")[:40])
        expect(self.page.locator("#cumSrc summary")).to_have_text(self.L("src.fold", n=3))
        self.details("#compCard")
        expect(self.page.locator("#compCard details.more")).to_contain_text(self.L("ui.food.comp_badge"))
        expect(self.page.locator("#compTrend")).to_contain_text(self.Lpart("comp.trend", 0))
        if UI_LANG == "it":  # first weigh-in on 11 February: "dall'11 febbraio" in both texts of Details
            expect(self.page.locator("#compTrend")).to_contain_text("dall'11 febbraio")  # lang: it
            expect(self.page.locator("#compEnergy")).to_contain_text("dall'11 febbraio")  # lang: it
        expect(self.page.locator("#compFm")).to_contain_text("Gallagher 2000")
        # initial fat with one decimal (the changes keep two)
        fm = self.page.locator("#compFm").inner_text()
        self.assertRegex(fm, r"\d+[.,]\d kg \(\d+[.,]\d[–-]\d+[.,]\d\)")
        expect(self.page.locator("#compEnergy")).to_contain_text(self.Lpart("bc.energy.coherent", 1))
        self.assertNotRegex(self.page.locator("#compEnergy").inner_text(), r"(?<![\w])-\d")   # typographic minus only
        expect(self.page.locator("#compSignals li")).to_have_count(3)
        expect(self.page.locator("#compSignalsText")).to_contain_text("Wycherley 2012")
        expect(self.page.locator("#compChoice")).to_have_text(self.L("comp.choice", months=6, pct=70))
        expect(self.page.locator("#compCard details.more")).to_contain_text(self.L("comp.limits")[:40])
        expect(self.page.locator("#compSrc summary")).to_have_text(self.L("src.fold_coi", n=11, coi=1))
        self.page.locator("#compSrc summary").click()
        expect(self.page.locator("#compSrc .coi.warn")).to_have_count(1)                 # Refalo 2025, flagged
        self.shot("#compCard", "comp-details")
        self.shot("#cumCard", "cum-details")
        # no horizontal scroll at 360 and 412 px, Details open or closed; chart labels inside the charts
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertEqual(self.page.evaluate(OUTSIDE), [], width)
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width, width)
            for sel in ("#cumCard", "#compCard"):
                box = self.page.locator(sel).bounding_box()
                self.assertLessEqual(box["x"] + box["width"], width, (sel, width))
            for b in self.page.locator("#cumSwitch button").all():
                self.assertGreaterEqual(b.bounding_box()["height"], 44)

    def test_periods_and_start_date(self):
        self.seed(weights=False)
        self.open_food()
        self.page.click('#cumSwitch button[data-p="prev_week"]')
        expect(self.page.locator("#cumSwitch button.on")).to_have_text(self.L("cum.p.prev_week"))
        expect(self.page.locator("#cumPeriod")).to_have_text(self.L("cum.period", **{"from": "2026-03-02", "to": "2026-03-08"}))
        expect(self.page.locator("#compEmpty")).to_have_text(self.L("comp.short.period", span=21))
        self.details("#compCard")
        expect(self.page.locator("#compNeed")).to_contain_text(self.Lpart("bc.need"))
        self.page.click('#cumSwitch button[data-p="week"]')                      # Monday 9 March and today
        self.details("#cumCard")
        expect(self.page.locator("#cumExcluded")).to_contain_text(self.L("cum.today"))
        self.page.click('#cumSwitch button[data-p="since"]')
        expect(self.page.locator("#cumSinceRow")).to_be_visible()
        expect(self.page.locator("#cumEmpty")).to_have_text(self.L("cum.need_date"))
        self.page.fill("#cumSince", "2026-02-01")
        self.page.locator("#cumSince").dispatch_event("change")
        expect(self.page.locator("#cumPeriod")).to_have_text(self.L("cum.period", **{"from": "2026-02-01", "to": DAY.isoformat()}))
        self.assertIn({"from": "2026-02-01"}, self.posts)
        self.assertEqual(json.loads((self.state / "nutrition.json").read_text())["balance_from"], "2026-02-01")
        expect(self.page.locator("#cumPartialPill")).to_be_visible()              # 1-10 February without a diary
        expect(self.page.locator("#cumPartial")).to_be_visible()
        # period and start date stay after a reload: they are saved in the data (nutrition.json)
        self.assertEqual(json.loads((self.state / "nutrition.json").read_text())["balance_period"], "since")
        self.open_food()
        expect(self.page.locator("#cumSwitch button.on")).to_have_text(self.L("cum.p.since"))
        expect(self.page.locator("#cumSince")).to_have_value("2026-02-01")
        # Italian: "dall'11", also in Details (energy check and line of the weigh-ins)
        self.page.click('#cumSwitch button[data-p="28d"]')
        expect(self.page.locator("#cumSwitch button.on")).to_have_text(self.L("cum.p.28d"))

    def test_measurement_from_the_profile(self):
        self.seed()
        self.page.click("#avatar")
        self.page.evaluate("openProfile()")
        expect(self.page.locator("#profSheet")).to_be_visible()
        self.page.fill("#profBf", "21.5")
        self.page.fill("#profBfDate", "2026-02-01")
        self.page.select_option("#profBfMethod", "dexa")
        self.page.click("#profSave")
        expect(self.page.locator("#profSheet")).to_be_hidden()
        saved = json.loads((self.state / "profile.json").read_text())
        self.assertEqual((saved["bf_pct"], saved["bf_date"], saved["bf_method"]), (21.5, "2026-02-01", "dexa"))
        self.open_food()
        self.details("#compCard")
        expect(self.page.locator("#compFm")).to_contain_text(self.L("comp.method.dexa"))
        expect(self.page.locator("#compFm")).to_contain_text("3.2" if UI_LANG == "en" else "3,2")
        # value without a method: refused, nothing saved
        self.page.evaluate("openProfile()")
        self.page.select_option("#profBfMethod", "")
        self.page.click("#profSave")
        expect(self.page.locator("#profErr")).to_contain_text(self.L("err.bf_incomplete"))
        self.assertEqual(json.loads((self.state / "profile.json").read_text())["bf_method"], "dexa")


if __name__ == "__main__":
    unittest.main()
