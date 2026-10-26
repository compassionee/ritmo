"""Browser tests of the Food tab (foods, recipes, saved meals, diary, complete day, analysis) and of the
session load in the sheets (workout and plan). Like test_ui.py: Chromium with Playwright, skipped on the server.
"""
import json
import unittest
import urllib.request
from datetime import timedelta

from test_helio import DAY
from test_ui import LOCALE, NOW, UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

OATS = {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "sat": 1.3, "salt": 0.02}
MILK = {"kcal": 46, "protein": 3.4, "carbs": 4.9, "fat": 1.5, "fiber": 0, "sat": 1.0, "salt": 0.1}


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class FoodUI(UIBase):
    # ------------------------------------------------------------ helpers
    def api(self, path, body):
        req = urllib.request.Request(self.base.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Helio": "1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def food(self, name, per100, **kw):
        return self.api("/api/food", {"food": {"name": name, "per100": per100, **kw}})["id"]

    def reload(self):
        self.h._daycache.clear()
        self.page.reload()
        expect(self.page.locator("#workoutList .card").first).to_be_attached()

    def cibo(self):
        self.page.click('button.tabbtn[data-tab="food"]')
        expect(self.page.locator("#tab-food")).to_be_visible()

    def toast(self, text):
        expect(self.page.locator("#toast")).to_contain_text(text)

    def meal_card(self, name):
        return self.page.locator("#nutMeals .card.meal").filter(has=self.page.get_by_text(name, exact=True))

    def chk(self, box, title):
        """Row of a check with that title (the sources inside the row can mention other nutrients)."""
        return self.page.locator(f"{box} .chk").filter(has=self.page.locator(".row b").get_by_text(title, exact=True))

    def diary(self, d=DAY):
        f = self.state / f"diary-{d:%Y-%m}.json"
        return json.loads(f.read_text()).get(d.isoformat()) if f.exists() else None

    # ------------------------------------------------------------ tab
    def test_empty_tab(self):
        self.cibo()
        expect(self.page.locator("#title")).to_have_text(self.L("title.food"))
        expect(self.page.locator("#nutKcal")).to_have_text("0")
        expect(self.page.locator("#nutRange")).to_have_text(self.L("nut.no_food_day"))
        expect(self.page.locator("#nutExp")).to_contain_text(self.Lpart("kcal.at_least"))  # today: the expenditure is a minimum
        expect(self.page.locator("#nutMeals .card.meal")).to_have_count(4)
        expect(self.page.locator("#nutWeekTitle")).to_contain_text(self.L("nut.need_days", need=3, have=0))
        expect(self.page.locator("#foodsList")).to_contain_text(self.L("food.no_foods"))
        expect(self.page.locator("#nutAthleteCard .row .coi.warn")).to_have_text(self.L("ui.food.flagged_reference"))
        expect(self.page.locator("#nutAthleteSrc")).to_contain_text("Thomas, Erdman, Burke 2016")
        expect(self.page.locator("#goalSwitch button.on")).to_have_text(self.L("ui.food.lose_fat"))
        expect(self.page.locator("#nutRecoveryCard")).to_be_hidden()

    def test_create_food_and_add_it_to_lunch(self):
        self.cibo()
        self.page.click("#newFood")
        expect(self.page.locator("#foodSheet")).to_be_visible()
        self.page.fill("#foodName", "Rolled oats")
        for sel, v in (("#fKcal", "372"), ("#fProtein", "13,5"), ("#fCarbs", "58.7"), ("#fFat", "7"), ("#fFiber", "10")):
            self.page.fill(sel, v.replace(",", "."))
        expect(self.page.locator("#foodCheck")).to_contain_text(self.Lpart("food.check_ok"))
        self.page.click("#foodOpt summary")
        self.page.fill("#fSat", "1.3")
        self.page.click("#foodAddPortion")
        self.page.locator("#foodPortions .pt-name").fill("1 cup")
        self.page.locator("#foodPortions .pt-g").fill("40")
        self.page.click("#foodSave")
        self.toast(self.L("food.saved"))
        expect(self.page.locator("#foodsList .food-row")).to_have_count(1)
        expect(self.page.locator("#foodsList")).to_contain_text(self.L("food.per100_line", kcal=372, p=13.5, c=58.7, f=7, fi=10))
        expect(self.page.locator("#foodsList .food-row .pill")).to_have_text(self.L("food.without", what=[self.L("food.missing.salt")]))
        saved = json.loads((self.state / "foods.json").read_text())["foods"]
        f = next(iter(saved.values()))
        self.assertEqual((f["name"], f["per100"]["protein"], f["per100"]["sat"], f["portions"]), ("Rolled oats", 13.5, 1.3, [["1 cup", 40]]))

        self.meal_card(self.L("meal.lunch")).locator(".add-btn").click()
        expect(self.page.locator("#pickTitle")).to_have_text(self.L("food.add_to", meal=self.L("meal.lunch").lower()))
        self.page.locator("#pickList .pick-row", has_text="Rolled oats").click()
        expect(self.page.locator("#entrySheet")).to_be_visible()
        expect(self.page.locator("#entryMeals button.on")).to_have_text(self.L("meal.lunch"))
        self.page.locator("#entryPortions button", has_text="1 cup").click()       # portion: 40 g
        expect(self.page.locator("#entryG")).to_have_value("40")
        self.page.fill("#entryG", "150")
        expect(self.page.locator("#entryPreview")).to_contain_text(self.L("food.entry_preview", kcal=558, p=20.25, c=88.05, f=10.5, fi=15).split(" · ")[1])
        self.page.click("#entrySave")
        self.toast(self.L("food.added", name="Rolled oats"))
        expect(self.meal_card(self.L("meal.lunch"))).to_contain_text(self.L("nut.entry_line", g=150, kcal=558))
        expect(self.page.locator("#nutKcal")).to_have_text("558")
        expect(self.page.locator("#nutSplitLegend")).to_contain_text(self.L("nut.carbs"))
        e = self.diary()["entries"][0]
        self.assertEqual((e["name"], e["g"], e["meal"]), ("Rolled oats", 150, "lunch"))
        # day in progress: no judgement on minimums, only what is missing
        prot = self.chk("#nutDayChecks", self.L("nutr.title.protein"))
        expect(prot).to_contain_text(self.Lpart("nut.missing_for"))
        expect(prot.locator(".pill")).to_have_count(0)

    def test_label_check_and_validation(self):
        self.cibo()
        self.page.click("#newFood")
        self.page.fill("#foodName", "Pasta")
        for sel, v in (("#fKcal", "35.9"), ("#fProtein", "12.5"), ("#fCarbs", "71.2"), ("#fFat", "2"), ("#fFiber", "3")):
            self.page.fill(sel, v)
        expect(self.page.locator("#foodCheck")).to_contain_text(self.L("food.check_bad", kcal=359, label=35.9, diff=323)[:50])
        expect(self.page.locator("#foodCheck")).to_have_class("note bad")
        self.page.fill("#fKcal", "359")
        expect(self.page.locator("#foodCheck")).to_contain_text(self.Lpart("food.check_ok"))
        self.page.fill("#fFiber", "")
        self.posts.clear()
        self.page.click("#foodSave")
        expect(self.page.locator("#foodErr")).to_contain_text(self.L("v.food_base"))
        self.page.fill("#foodName", "")
        self.page.click("#foodSave")
        expect(self.page.locator("#foodErr")).to_have_text(self.L("v.food_name"))
        self.assertEqual(self.posts, [])
        self.page.fill("#foodName", "Pasta")
        self.page.fill("#fFiber", "3")
        self.page.fill("#fProtein", "500")                                              # the server rejects it
        self.page.click("#foodSave")
        expect(self.page.locator("#foodErr")).to_contain_text(self.L("err.not_saved", msg=self.L("e.value_range", field=self.L("nutr.protein"), lo=0, hi=100)))
        expect(self.page.locator("#foodSave")).to_have_text(self.L("common.retry"))
        expect(self.page.locator("#foodSave")).to_be_enabled()

    def test_new_food_from_the_add_sheet_goes_straight_to_grams(self):
        self.cibo()
        self.meal_card(self.L("meal.dinner")).locator(".add-btn").click()
        self.page.fill("#pickSearch", "Greek yogurt")
        expect(self.page.locator("#pickList")).to_contain_text(self.L("food.pick_none"))
        self.page.click("#pickNewFood")
        expect(self.page.locator("#foodName")).to_have_value("Greek yogurt")
        for sel, v in (("#fKcal", "97"), ("#fProtein", "9"), ("#fCarbs", "4"), ("#fFat", "5"), ("#fFiber", "0")):
            self.page.fill(sel, v)
        self.page.click("#foodSave")
        expect(self.page.locator("#entrySheet")).to_be_visible()
        expect(self.page.locator("#entryTitle")).to_have_text("Greek yogurt")
        expect(self.page.locator("#entryMeals button.on")).to_have_text(self.L("meal.dinner"))
        self.page.fill("#entryG", "170")
        self.page.click("#entrySave")
        expect(self.meal_card(self.L("meal.dinner"))).to_contain_text(self.L("nut.entry_line", g=170, kcal=165))

    def test_edit_move_and_remove_an_entry(self):
        oats = self.food("Oats", OATS)
        self.api("/api/diary", {"date": DAY.isoformat(), "op": "add", "meal": "breakfast", "food": oats, "g": 60})
        self.reload()
        self.cibo()
        self.meal_card(self.L("meal.breakfast")).locator(".meal-entry").click()
        expect(self.page.locator("#entrySub")).to_contain_text(self.L("food.entry_copied"))
        expect(self.page.locator("#entryG")).to_have_value("60")
        self.page.fill("#entryG", "80")
        self.page.locator("#entryMeals button", has_text=self.L("meal.snacks")).click()
        self.page.click("#entrySave")
        self.toast(self.L("food.entry_updated"))
        expect(self.meal_card(self.L("meal.snacks"))).to_contain_text(self.L("nut.entry_line", g=80, kcal=298))
        expect(self.meal_card(self.L("meal.breakfast")).locator(".meal-entry")).to_have_count(0)
        self.meal_card(self.L("meal.snacks")).locator(".meal-entry").click()
        self.page.click("#entryRemove")
        self.toast(self.L("food.entry_removed"))
        expect(self.page.locator("#nutMeals .meal-entry")).to_have_count(0)
        self.assertIsNone(self.diary())

    def test_recipe_editor(self):
        oats, milk = self.food("Rolled oats", OATS), self.food("Milk", MILK)
        self.reload()
        self.cibo()
        self.page.click("#newRecipe")
        self.page.fill("#recipeName", "Porridge")
        rows = self.page.locator("#recipeItems .it-row")
        rows.nth(0).locator(".it-name").fill("Rolled oats")
        rows.nth(0).locator(".it-g").fill("50")
        self.page.click("#recipeAddItem")
        rows.nth(1).locator(".it-name").fill("milk")                                   # case does not matter
        rows.nth(1).locator(".it-g").fill("200")
        self.page.fill("#recipeCooked", "260")
        # (372 × 0.5 + 46 × 2) = 278 kcal in 260 g: 107 kcal per 100 g
        expect(self.page.locator("#recipePreview")).to_contain_text(self.L("recipe.preview", kcal=278, cooked=260, raw=250, kcal100=107, p=0, c=0, f=0, fi=0).split(", ")[0])
        self.page.click("#recipeAddItem")
        rows.nth(2).locator(".it-name").fill("Honey")
        rows.nth(2).locator(".it-g").fill("10")
        self.page.click("#recipeSave")
        expect(self.page.locator("#recipeErr")).to_contain_text(self.L("v.ingredients_bad", names="Honey"))
        rows.nth(2).locator(".pe-btn").click()
        self.page.click("#recipeSave")
        self.toast(self.L("recipe.saved"))
        self.page.locator('#foodsSwitch button[data-k="recipe"]').click()
        expect(self.page.locator("#foodsList")).to_contain_text("Porridge")
        expect(self.page.locator("#foodsList")).to_contain_text("107 kcal")
        rec = [f for f in json.loads((self.state / "foods.json").read_text())["foods"].values() if f["kind"] == "recipe"][0]
        self.assertEqual((rec["items"], rec["cooked_g"]), ([[oats, 50], [milk, 200]], 260))

    def test_meal_template_from_the_diary_and_reuse(self):
        oats, milk = self.food("Oats", OATS), self.food("Milk", MILK)
        for fid, g in ((oats, 60), (milk, 250)):
            self.api("/api/diary", {"date": DAY.isoformat(), "op": "add", "meal": "breakfast", "food": fid, "g": g})
        self.reload()
        self.cibo()
        self.meal_card(self.L("meal.breakfast")).locator("button", has_text=self.L("nut.save_as_meal")).click()
        expect(self.page.locator("#mealName")).to_have_value(self.L("meal.default_name", meal=self.L("meal.breakfast")))
        expect(self.page.locator("#mealItems .it-row")).to_have_count(2)
        expect(self.page.locator("#mealPreview")).to_contain_text(self.L("meal.preview", n=2, kcal=338))
        self.page.click("#mealSave")
        self.toast(self.L("meal.saved"))
        meal = next(iter(json.loads((self.state / "foods.json").read_text())["meals"].values()))
        self.assertEqual((meal["name"], meal["meal"], meal["items"]), (self.L("meal.default_name", meal=self.L("meal.breakfast")), "breakfast", [[oats, 60], [milk, 250]]))
        expect(self.page.locator("#foodsCount")).to_contain_text(self.L("food.n_meals", n=1))
        self.page.click("#prev")                                                         # yesterday: one tap for the whole breakfast
        expect(self.page.locator("#dayLabel")).to_have_text(self.L("day.yesterday"))
        expect(self.meal_card(self.L("meal.breakfast")).locator(".meal-entry")).to_have_count(0)
        self.meal_card(self.L("meal.breakfast")).locator(".add-btn").click()
        self.page.locator("#pickList .pick-row", has_text=self.L("meal.default_name", meal=self.L("meal.breakfast"))).click()
        self.toast(self.L("food.added", name=self.L("meal.default_name", meal=self.L("meal.breakfast"))))
        expect(self.meal_card(self.L("meal.breakfast")).locator(".meal-entry")).to_have_count(2)
        self.assertEqual(len(self.diary(DAY - timedelta(days=1))["entries"]), 2)

    def test_complete_day_and_goal(self):
        oats = self.food("Oats", OATS)
        self.api("/api/diary", {"date": DAY.isoformat(), "op": "add", "meal": "lunch", "food": oats, "g": 100})
        self.reload()
        self.cibo()
        self.page.click("#nutComplete")
        self.toast(self.L("nut.completed"))
        expect(self.page.locator("#nutComplete")).to_have_text(self.L("nut.day_complete"))
        self.assertTrue(self.diary()["complete"])
        expect(self.page.locator("#nutDaySub")).to_have_text(self.L("nut.day_complete_lc"))
        prot = self.chk("#nutDayChecks", self.L("nutr.title.protein"))
        expect(prot.locator(".pill")).to_contain_text(self.L("nut.label_status", label=self.L("nc.protein.below_efsa"), status=self.L("nut.st.certain")))   # 13.5 g
        expect(prot).to_contain_text(self.page.evaluate("I18N.num(1.9, 1)") + " g/kg")
        self.page.locator("#goalSwitch button", has_text=self.L("ui.food.maintain")).click()
        self.toast(self.L("nut.goal_set", goal=self.L("ui.food.maintain").lower()))
        expect(self.page.locator("#goalSwitch button.on")).to_have_text(self.L("ui.food.maintain"))
        expect(self.chk("#nutDayChecks", self.L("nutr.title.protein"))).to_contain_text(self.page.evaluate("I18N.num(1.6, 1)") + " g/kg")
        self.assertEqual(json.loads((self.state / "nutrition.json").read_text())["goal"], "maintain")
        self.page.click("#nutComplete")
        self.toast(self.L("nut.reopened"))
        expect(self.page.locator("#nutComplete")).to_have_text(self.L("ui.food.mark_day_as_complete"))

    def test_week_analysis_and_insight_on_today(self):
        low = self.food("Proteico", {"kcal": 150, "protein": 20, "carbs": 15, "fat": 1.3, "fiber": 0.2})
        for i in range(1, 4):
            d = (DAY - timedelta(days=i)).isoformat()
            self.api("/api/diary", {"date": d, "op": "add", "meal": "lunch", "food": low, "g": 1000})
            self.api("/api/diary", {"date": d, "op": "complete", "complete": True})
        self.reload()
        card = self.page.locator("#insights .card", has_text=self.L("tag.nutrition"))
        expect(card).to_contain_text(self.L("ins.nut.title", name=self.L("nutr.title.fiber"), label=self.L("nc.fiber.below")))
        card.locator("button.go").click()
        expect(self.page.locator("#tab-food")).to_be_visible()
        expect(self.page.locator("#nutWeekTitle")).to_contain_text(self.Lpart("nut.issues"))
        fiber = self.chk("#nutWeekChecks", self.L("nutr.title.fiber"))
        expect(fiber.locator(".pill")).to_have_text(self.L("nut.label_status", label=self.L("nc.fiber.below"), status=self.L("nut.st.certain")))
        expect(fiber).to_contain_text(self.L("nc.fiber.text")[:30])
        fiber.locator("summary").click()
        expect(fiber.locator("a").first).to_contain_text("WHO 2023")
        expect(self.page.locator("#nutWeekChart svg rect.bar-food")).to_have_count(3)

    def test_correct_a_food_also_in_the_past(self):
        oats = self.food("Oats", OATS)
        self.api("/api/diary", {"date": DAY.isoformat(), "op": "add", "meal": "lunch", "food": oats, "g": 100})
        self.reload()
        self.cibo()
        self.page.locator("#foodsList .food-row", has_text="Oats").click()
        expect(self.page.locator("#foodTitle")).to_have_text(self.L("food.edit"))
        expect(self.page.locator("#fProtein")).to_have_value("13.5")
        expect(self.page.locator("#foodOpt")).to_have_attribute("open", "")              # it has saturated fat and salt
        self.page.fill("#fKcal", "380")
        self.page.check("#foodPast")
        self.page.click("#foodSave")
        self.toast(self.L("food.saved_fixed", what=self.L("food.saved"), n=1))
        expect(self.meal_card(self.L("meal.lunch"))).to_contain_text(self.L("nut.entry_line", g=100, kcal=380))

    def test_food_in_use_cannot_be_deleted(self):
        oats, milk = self.food("Oats", OATS), self.food("Milk", MILK)
        self.api("/api/food", {"food": {"kind": "recipe", "name": "Porridge", "items": [[oats, 50], [milk, 200]]}})
        self.reload()
        self.cibo()
        self.page.locator("#foodsList .food-row", has_text="Oats").click()
        self.page.click("#foodDelete")
        expect(self.page.locator("#foodErr")).to_contain_text(self.Lpart("e.food_in_use") + ": Porridge")
        self.page.click("#foodClose")
        self.page.locator("#foodsList .food-row", has_text="Milk").click()
        self.page.route("**/api/food", lambda r: r.fulfill(status=500, content_type="application/json",
                                                            body=json.dumps({"error": "disk full"})))
        self.page.click("#foodSave")
        expect(self.page.locator("#foodErr")).to_have_text(self.L("err.not_saved", msg="disk full"))
        expect(self.page.locator("#foodSave")).to_have_text(self.L("common.retry"))
        self.page.unroute("**/api/food")

    def test_tab_survives_a_failed_nutrition_request(self):
        self.page.route("**/api/nutrition*", lambda r: r.fulfill(status=500, body="{}", content_type="application/json"))
        self.page.click("#prev")
        expect(self.page.locator("#dayLabel")).to_have_text(self.L("day.yesterday"))
        expect(self.page.locator("#error")).to_be_hidden()                            # the day loads anyway
        self.cibo()
        expect(self.page.locator("#nutError")).to_be_visible()
        expect(self.page.locator("#nutHero")).to_be_hidden()
        self.page.unroute("**/api/nutrition*")
        self.errors.clear()

    # ------------------------------------------------------------ added load in the sheets
    def test_confirm_strength_uses_the_plan_load(self):
        def patch(route):
            resp = route.fetch()
            body = resp.json()
            body["trainings"][-1]["suggestion"].update({"type": "strength", "day": 2, "name": "Strength training · day 2", "load_kg": 15})  # server-side name, not checked
            route.fulfill(response=resp, json=body)
        self.page.route("**/api/summary*", patch, times=1)                           # only the first load
        self.page.reload()
        self.page.click('button.tabbtn[data-tab="fitness"]')
        card = self.page.locator(".card.workout").first
        expect(card).to_contain_text(self.Lpart("fit.looks_like"))
        card.locator("button", has_text=self.L("fit.confirm")).click()
        self.toast(self.L("common.saved"))
        expect(self.page.locator(".card.workout").first).to_contain_text(self.L("fit.confirmed"))
        self.assertEqual((self.posts[-1]["type"], self.posts[-1]["day"], self.posts[-1]["load_kg"]), ("strength", 2, 15))
        lb = next(iter(json.loads((self.state / "labels.json").read_text()).values()))
        self.assertEqual((lb["type"], lb["load_kg"]), ("strength", 15))
        self.page.unroute_all(behavior="wait")

    def test_training_sheet_prefills_the_load_of_the_strength_day(self):
        self.api("/api/plan", {"plan": {"2": {"text": "Strength training – day 2", "types": ["strength"], "day": 2, "load_kg": 15,
                                              "exercises": [["Plank", "3×1 min"]]}}})
        self.reload()
        self.page.click('button.tabbtn[data-tab="fitness"]')
        self.page.locator(".card.workout").first.locator("button", has_text=self.L("fit.change")).click()
        self.page.locator(".type-btn", has_text=self.L("act.strength")).click()
        expect(self.page.locator("#sheetDays button.on")).to_have_text(self.L("plan.strength_day_n", n=2))
        expect(self.page.locator("#sheetLoad")).to_have_value("15")
        self.page.locator("#sheetDays button", has_text=self.L("plan.strength_day_n", n=1)).click()             # day 1 is not in the plan
        expect(self.page.locator("#sheetLoad")).to_have_value("")
        self.page.locator("#sheetDays button", has_text=self.L("plan.strength_day_n", n=2)).click()
        self.page.fill("#sheetLoad", "18")                                               # chosen by hand: it stays
        self.page.locator("#sheetDays button", has_text=self.L("plan.strength_day_n", n=1)).click()
        expect(self.page.locator("#sheetLoad")).to_have_value("18")
        self.page.click("#sheetSave")
        expect(self.page.locator(".card.workout").first).to_contain_text(self.L("fit.load_kg", kg=18))

    def test_plan_editor_load(self):
        self.page.click("#avatar")
        self.page.click("#planOpen")
        expect(self.page.locator("#planLoadRow")).to_be_hidden()                       # no strength: no added load
        self.page.locator("#planTypes button", has_text=self.L("act.strength")).click()
        expect(self.page.locator("#planLoadRow")).to_be_visible()
        self.page.locator("#planStrengthDay button", has_text=self.L("plan.strength_day_n", n=1)).click()
        self.page.fill("#planLoad", "15")
        self.page.locator("#planDays button").nth(0).click()                            # Monday and back: the value stays
        self.page.locator("#planDays button").nth(1).click()
        expect(self.page.locator("#planLoad")).to_have_value("15")
        self.page.click("#planSave")
        self.toast(self.L("plan.saved"))
        self.assertEqual(json.loads((self.state / "plan.json").read_text())["2"]["load_kg"], 15)

    # ------------------------------------------------------------ swiping between days
    def test_swipe_changes_day_only_outside_sheets_and_chips(self):
        ctx = self.browser.new_context(viewport={"width": 412, "height": 915}, has_touch=True, timezone_id="Europe/Rome",
                                       locale=LOCALE, service_workers="block")
        pg = ctx.new_page()
        pg.clock.set_fixed_time(NOW)
        pg.goto(self.base)
        self.wait_catalog(pg)
        expect(pg.locator("#dayLabel")).to_have_text(self.L("day.today", page=pg))
        expect(pg.locator("#workoutList .card").first).to_be_attached()               # data loaded
        swipe = """([sel, x0, x1]) => {
            const el = document.querySelector(sel);
            const t = (x) => new Touch({identifier: 1, target: el, clientX: x, clientY: 400});
            el.dispatchEvent(new TouchEvent("touchstart", {touches: [t(x0)], changedTouches: [t(x0)], bubbles: true}));
            el.dispatchEvent(new TouchEvent("touchend", {touches: [], changedTouches: [t(x1)], bubbles: true}));
        }"""
        try:
            pg.evaluate(swipe, ["#tab-today", 40, 320])
            expect(pg.locator("#dayLabel")).to_have_text(self.L("day.yesterday"))
            pg.click('button.tabbtn[data-tab="food"]')
            pg.click("#newFood")
            expect(pg.locator("#foodSheet")).to_be_visible()
            pg.evaluate(swipe, ["#foodSheet .sheet-body", 40, 320])                      # sheet open: no change
            expect(pg.locator("#dayLabel")).to_have_text(self.L("day.yesterday"))
            pg.click("#foodClose")
            pg.evaluate(swipe, ["#goalSwitch", 40, 320])                                 # buttons side by side: no change
            expect(pg.locator("#dayLabel")).to_have_text(self.L("day.yesterday"))
            pg.evaluate(swipe, ["#tab-food", 320, 40])
            expect(pg.locator("#dayLabel")).to_have_text(self.L("day.today", page=pg))
        finally:
            ctx.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
