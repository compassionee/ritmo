"""Browser tests of the food catalog: search in the "Add" sheet (my foods first, then the community with the
source of every value), no request leaves the page's own server while searching, community foods in the diary
without saving them, "Save to my foods", typed barcodes, the Food database card in Profile. Like test_ui.py:
Chromium with Playwright, skipped on the server."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from test_catalog import build, sample_off
from test_helio import DAY
from test_ui import UI_LANG, UIBase, sync_playwright
import test_ui_food  # helpers only: importing the class here would run its tests twice

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

OATS = {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10}


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class CatalogUI(UIBase):
    api = test_ui_food.FoodUI.api
    food = test_ui_food.FoodUI.food
    cibo = test_ui_food.FoodUI.cibo
    toast = test_ui_food.FoodUI.toast
    diary = test_ui_food.FoodUI.diary
    meal_card = test_ui_food.FoodUI.meal_card

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._pkg = tempfile.TemporaryDirectory()
        cls.h.catalog_install(build(cls._pkg.name, sample_off(), kind="off"))

    @classmethod
    def tearDownClass(cls):
        cls._pkg.cleanup()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.outside = []
        base = urlparse(self.base)

        def guard(route):
            u = urlparse(route.request.url)
            if (u.hostname, u.port) != (base.hostname, base.port):
                self.outside.append(route.request.url)
                return route.abort()
            return route.continue_()

        self.page.route("**/*", guard)
        self.requests = []
        self.page.on("request", lambda r: self.requests.append(r.url))

    def open_pick(self, meal="breakfast"):
        self.cibo()
        self.page.locator(f'#nutMeals button.add-btn[data-meal="{meal}"]').click()
        expect(self.page.locator("#pickSheet")).to_be_visible()

    def search(self, q):
        self.requests.clear()
        self.page.fill("#pickSearch", q)

    def first_community(self, q):
        return self.h.catalog_search(q, UI_LANG)["community"][0]

    def test_my_foods_first_then_the_community_with_its_source(self):
        self.food("Mela Golden di casa", OATS)
        self.page.reload()
        self.wait_catalog()
        self.open_pick()
        self.search("mela")
        rows = self.page.locator("#pickList .pick-row")
        expect(rows.first).to_contain_text("Mela Golden di casa")
        first = self.first_community("mela")
        cat_row = self.page.locator("#pickCat .pick-row").first
        expect(cat_row).to_contain_text(first["name"])
        expect(cat_row.locator(".src-tag")).to_have_text(self.L(f"cat.src.{first['src']}"))
        # the list of my foods comes before the community block in the sheet
        self.assertLess(self.page.locator("#pickList").bounding_box()["y"], self.page.locator("#pickCat").bounding_box()["y"])

    def test_search_never_leaves_the_server(self):
        self.open_pick()
        for q in ("me", "mel", "mela", "biscotti", "caffe", "8001234567897", "zzzz"):
            self.search(q)
            self.page.wait_for_timeout(300)
        expect(self.page.locator("#pickCat")).to_contain_text(self.L("cat.none"))
        self.assertEqual(self.outside, [])
        paths = {urlparse(u).path for u in self.requests}
        self.assertTrue(paths <= {"/api/catalog/search", "/api/catalog/barcode"}, paths)

    def test_add_a_community_food_without_saving_it(self):
        self.open_pick("lunch")
        self.search("mela snack")
        it = self.first_community("mela snack")
        self.page.locator("#pickCat .pick-row").first.click()
        expect(self.page.locator("#entrySheet")).to_be_visible()
        expect(self.page.locator("#entrySub")).to_contain_text("2026-10-21")
        values = self.page.locator("#entryValues")
        expect(values).to_contain_text(self.L("cat.na"))  # no fibre in the source
        expect(values.locator(".src-tag").first).to_have_text("OFF")
        self.page.fill("#entryG", "200")
        expect(self.page.locator("#entryPreview")).to_contain_text(self.Lpart("cat.entry_preview_nofi", -1))
        self.page.click("#entrySave")
        expect(self.page.locator("#entrySheet")).to_be_hidden()
        e = self.diary()["entries"][0]
        self.assertEqual((e["food"], e["src"]["src"], e["src"]["ref"], e["g"]), (None, "off", it["ref"], 200))
        self.assertFalse((self.state / "foods.json").exists())  # not saved among my foods
        expect(self.meal_card(self.L("meal.lunch")).locator(".src-tag")).to_have_text("OFF")
        # complete day: the fibre is only a minimum, shown as "at least"
        self.page.click("#nutComplete")
        fiber = self.page.locator("#nutDayChecks .chk").filter(has=self.page.get_by_text(self.L("nutr.title.fiber"), exact=True))
        expect(fiber).to_contain_text(self.Lpart("nc.at_least", 0))

    def test_save_to_my_foods_as_a_copy(self):
        self.open_pick("dinner")
        self.search("8001234567897")
        row = self.page.locator("#pickCat .pick-row").first
        expect(row).to_contain_text("Biscotti integrali")
        expect(row.locator(".src-tag")).to_have_text("OFF")
        row.click()
        self.page.click("#entrySaveMine")
        expect(self.page.locator("#foodSheet")).to_be_visible()
        expect(self.page.locator("#foodOrigin")).to_contain_text("OFF")
        expect(self.page.locator("#foodName")).to_have_value("Biscotti integrali (Marca)")
        expect(self.page.locator("#fProtein")).to_have_value("7.5")
        expect(self.page.locator("#fProtein").locator("xpath=..").locator(".src-tag")).to_have_text("OFF")
        self.page.fill("#fProtein", "8")  # corrected: that value becomes mine
        expect(self.page.locator("#fProtein").locator("xpath=..").locator(".src-tag")).to_have_text(self.L("cat.src.user"))
        self.page.click("#foodSave")
        expect(self.page.locator("#entrySheet")).to_be_visible()  # back to the grams, with my copy
        foods = json.loads((self.state / "foods.json").read_text())["foods"]
        (f,) = foods.values()
        self.assertEqual((f["origin"]["src"], f["origin"]["ref"], f["barcode"], f["per100"]["protein"]),
                         ("off", "8001234567897", "8001234567897", 8.0))
        self.page.click("#entryClose")
        # my copy now comes first for the same barcode, the community product stays
        self.open_pick("dinner")
        self.search("8001234567897")
        expect(self.page.locator("#pickCat .pick-row")).to_have_count(2)
        expect(self.page.locator("#pickCat .pick-row").first).not_to_contain_text("OFF")

    def test_scanned_code_is_shown_editable_before_anything(self):
        """In the app the camera sends the digits back with window.helioScanned; in the browser the button is hidden.
        The number read stays in sight and can be corrected; nothing opens nor is added by itself."""
        self.open_pick()
        expect(self.page.locator("#pickScan")).to_be_hidden()
        expect(self.page.locator("#pickScanned")).to_be_hidden()
        self.requests.clear()
        self.page.evaluate("window.helioScanned({code: '8001234567897'})")
        expect(self.page.locator("#pickScanned")).to_be_visible()
        expect(self.page.locator("#pickScanned")).to_contain_text(self.L("cat.scanned"))
        expect(self.page.locator("#scanCode")).to_have_value("8001234567897")
        expect(self.page.locator("#scanAgain")).to_have_text(self.L("cat.scan_again"))
        expect(self.page.locator("#pickList")).to_be_hidden()
        rows = self.page.locator("#pickCat .pick-row")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("Biscotti integrali")
        expect(self.page.locator("#entrySheet")).to_be_hidden()
        self.assertFalse(list(self.state.glob("diary-*.json")))
        self.assertEqual({urlparse(u).path for u in self.requests}, {"/api/catalog/barcode"})
        # the number and the button fit a small phone, in both languages
        self.page.set_viewport_size({"width": 360, "height": 780})
        btn = self.page.locator("#scanAgain").bounding_box()
        self.assertLessEqual(btn["x"] + btn["width"], 360)
        self.assertTrue(self.page.evaluate("(e => e.scrollWidth <= e.clientWidth)(document.getElementById('scanCode'))"),
                        "all 13 digits in sight")
        if os.environ.get("HELIO_SHOTS"):
            self.page.locator("#pickSheet .sheet-body").screenshot(path=f"{os.environ['HELIO_SHOTS']}/scan-{UI_LANG}.png")
        self.page.set_viewport_size({"width": 412, "height": 915})
        # one digit corrected the wrong way: the check digit says so at once, without asking the server
        self.requests.clear()
        self.page.fill("#scanCode", "8001234567807")
        expect(self.page.locator("#pickCat")).to_contain_text(self.Lpart("cat.code_check", 0))
        expect(self.page.locator("#pickCat .pick-row")).to_have_count(0)
        self.assertEqual([u for u in self.requests if "/api/catalog" in u], [])
        self.page.fill("#scanCode", "8001 2345-67897")  # only digits are kept
        expect(self.page.locator("#scanCode")).to_have_value("8001234567897")
        expect(rows).to_have_count(1)
        rows.first.click()
        expect(self.page.locator("#entryTitle")).to_have_text("Biscotti integrali (Marca)")
        expect(self.page.locator("#entrySub")).to_contain_text("Marca")
        self.page.click("#entryClose")
        # scan again asks the app for the camera; typing in the search goes back to the normal list
        self.open_pick()
        self.page.evaluate("window.helioScanned({code: '8001234567897'})")
        self.requests.clear()
        self.page.click("#scanAgain")
        self.page.wait_for_timeout(200)
        self.assertIn("/api/android/action", {urlparse(u).path for u in self.requests})
        self.page.fill("#pickSearch", "mela")
        expect(self.page.locator("#pickScanned")).to_be_hidden()
        expect(self.page.locator("#pickList")).to_be_visible()
        self.page.evaluate("window.helioScanned({error: 'permission'})")
        self.toast(self.Lpart("cat.scan_permission", 0))
        self.assertEqual(self.outside, [])

    def fresh(self):
        self.page.reload()
        self.wait_catalog()

    def test_save_also_to_my_foods_is_remembered(self):
        self.open_pick("dinner")
        self.search("8001234567897")
        self.page.locator("#pickCat .pick-row").first.click()
        expect(self.page.locator("#entryKeepRow")).to_be_visible()
        expect(self.page.locator("#entryKeepRow")).to_contain_text(self.L("cat.save_also"))
        expect(self.page.locator("#entryKeep")).not_to_be_checked()
        self.page.check("#entryKeep")
        self.page.wait_for_function("() => SETTINGS.save_mine === true")
        self.assertTrue(json.loads((self.state / "settings.json").read_text())["save_mine"])
        self.page.fill("#entryG", "30")
        self.page.click("#entrySave")
        expect(self.page.locator("#entrySheet")).to_be_hidden()
        (f,) = json.loads((self.state / "foods.json").read_text())["foods"].values()
        self.assertEqual((f["name"], f["barcode"], f["origin"]["ref"]), ("Biscotti integrali (Marca)", "8001234567897", "8001234567897"))
        (e,) = self.diary()["entries"]
        self.assertEqual(e["food"], f["id"])
        # remembered after a reload; the same product again makes no second copy
        self.fresh()
        self.open_pick("dinner")
        self.search("8001234567897")
        self.page.locator("#pickCat .pick-row", has_text="OFF").click()
        expect(self.page.locator("#entryKeep")).to_be_checked()
        self.page.fill("#entryG", "20")
        self.page.click("#entrySave")
        expect(self.page.locator("#entrySheet")).to_be_hidden()
        self.assertEqual(len(json.loads((self.state / "foods.json").read_text())["foods"]), 1)
        self.assertEqual([x["food"] for x in self.diary()["entries"]], [f["id"], f["id"]])
        # a food of mine opens without the box
        self.open_pick("dinner")
        self.page.locator("#pickList .pick-row", has_text="Biscotti integrali").first.click()
        expect(self.page.locator("#entryKeepRow")).to_be_hidden()

    def test_scanned_code_of_my_food_opens_it(self):
        """My food with the UPC-A code; the scanner reads the EAN-13 form of it: my food opens, nothing else."""
        self.food("Mela snack di casa", OATS, barcode="012345678905")
        self.fresh()
        self.open_pick()
        self.requests.clear()
        self.page.evaluate("window.helioScanned({code: '0012345678905'})")
        expect(self.page.locator("#entrySheet")).to_be_visible()
        expect(self.page.locator("#entryTitle")).to_have_text("Mela snack di casa")
        expect(self.page.locator("#entrySub")).to_contain_text("0012345678905")
        expect(self.page.locator("#entryKeepRow")).to_be_hidden()
        self.assertEqual([u for u in self.requests if "/api/catalog" in u], [])

    def test_save_to_my_foods_uses_my_food_with_that_code(self):
        self.food("I miei biscotti", OATS, barcode="8001234567897")
        self.fresh()
        self.open_pick()
        self.search("8001234567897")
        self.page.locator("#pickCat .pick-row", has_text="OFF").click()
        self.page.click("#entrySaveMine")
        self.toast(self.Lpart("cat.already_mine", 0))
        expect(self.page.locator("#entryTitle")).to_have_text("I miei biscotti")
        expect(self.page.locator("#foodSheet")).to_be_hidden()
        self.assertEqual(len(json.loads((self.state / "foods.json").read_text())["foods"]), 1)

    def test_recent_on_top_then_my_foods_by_use(self):
        ids = {n: self.food(n, OATS) for n in ("Avena", "Banana", "Cracker", "Datteri")}
        tpl = self.api("/api/meal", {"meal": {"name": "Colazione solita", "meal": "breakfast", "items": [[ids["Avena"], 40]]}})["id"]
        day = DAY.isoformat()
        for name in ("Banana", "Banana", "Cracker", "Banana"):
            self.api("/api/diary", {"date": day, "op": "add", "meal": "lunch", "food": ids[name], "g": 50})
        self.api("/api/diary", {"date": day, "op": "add_meal", "meal": "breakfast", "template": tpl})
        self.fresh()
        self.open_pick()
        heads = self.page.locator("#pickList .pick-head").all_inner_texts()
        self.assertEqual(heads[0].lower(), self.L("food.recent").lower())
        rows = self.page.locator("#pickList .pick-row b").all_inner_texts()
        # Recent: the saved meal (last), Banana, Cracker; then the saved meals; then my foods by use
        self.assertEqual(rows[:3], ["Colazione solita", "Banana", "Cracker"])
        self.assertEqual(rows[3], "Colazione solita")
        self.assertEqual(rows[4:], ["Banana", "Cracker", "Avena", "Datteri"])
        # a search shows no Recent
        self.page.fill("#pickSearch", "a")
        self.assertNotIn(self.L("food.recent").lower(), [x.lower() for x in self.page.locator("#pickList .pick-head").all_inner_texts()])

    def test_typed_code_with_a_wrong_check_digit(self):
        self.open_pick()
        self.search("8001234567890")
        expect(self.page.locator("#pickCat")).to_contain_text("8001234567890")
        expect(self.page.locator("#pickCat")).to_contain_text(self.Lpart("cat.code_check", 0))
        self.assertEqual([u for u in self.requests if "/api/catalog" in u], [])
        self.search("8001234567897")
        expect(self.page.locator("#pickCat .pick-row")).to_have_count(1)

    def test_scanned_code_with_several_products_lets_me_choose(self):
        self.open_pick()
        self.page.evaluate("window.helioScanned({code: '0000020570910'})")
        rows = self.page.locator("#pickCat .pick-row")
        expect(rows).to_have_count(2)
        expect(self.page.locator("#pickCat")).to_contain_text("Bisfrolla con crema alle nocciole e cacao")
        expect(self.page.locator("#pickCat")).to_contain_text("Confiserie Firenze")
        cz = self.page.evaluate("new Intl.DisplayNames([I18N.locale()], {type: 'region'}).of('CZ')")
        expect(self.page.locator("#pickCat")).to_contain_text(cz)  # the country of each product
        expect(self.page.locator("#pickCat")).to_contain_text(self.Lpart("cat.country_gs1", 0))
        rows.filter(has_text="Bisfrolla").click()
        expect(self.page.locator("#entryTitle")).to_have_text("Bisfrolla con crema alle nocciole e cacao (Confiserie Firenze)")
        expect(self.page.locator("#entrySub")).to_contain_text(cz)

    def test_scanned_code_not_found_creates_a_food_with_it(self):
        self.open_pick("dinner")
        self.page.evaluate("window.helioScanned({code: '4000000000006'})")
        self.page.locator("#pickCat button.add-btn").click()
        for sel, v in (("#foodName", "Torta della nonna"), ("#fKcal", "350"), ("#fProtein", "6"), ("#fCarbs", "45"),
                       ("#fFat", "16"), ("#fFiber", "1")):
            self.page.fill(sel, v)
        self.page.click("#foodSave")
        expect(self.page.locator("#entrySheet")).to_be_visible()
        (f,) = json.loads((self.state / "foods.json").read_text())["foods"].values()
        self.assertEqual(f["barcode"], "4000000000006")

    def test_unknown_barcode(self):
        self.open_pick()
        self.search("4000000000006")
        expect(self.page.locator("#pickCat")).to_contain_text(self.Lpart("cat.code_none", 0))

    def test_food_database_card(self):
        self.page.click("#avatar")
        card = self.page.locator("#catalogCard")
        expect(card).to_be_visible()
        expect(card.locator("#catalogList li")).to_have_count(2)
        expect(card.locator("#catalogList")).to_contain_text("Open Food Facts")
        expect(card.locator("#catalogList")).to_contain_text("ODbL 1.0")
        expect(self.page.locator("#catalogImport")).to_be_hidden()  # in the browser: no file picker of the app
        expect(self.page.locator("#catalogServerNote")).to_be_visible()
        # only "Import from file": no address field nor "Update from URL" (Ritmo has no network)
        expect(card.locator("input[type=url], #catalogUrl, #catalogUrlBox, details:not(.more)")).to_have_count(0)
        expect(card.locator("details.more input")).to_have_count(0)                   # "Details" holds only the intro text
        self.assertNotIn("https://", card.inner_text())
        expect(self.page.locator("#catalogServerNote")).to_have_text(self.L("cat.server_import"))
        self.assertEqual(self.outside, [])


    def test_about_screen_attributions(self):
        self.page.click("#avatar")
        self.page.click("#aboutOpen")
        sheet = self.page.locator("#aboutSheet")
        expect(sheet).to_be_visible()
        expect(sheet.locator("#aboutApp a")).to_have_attribute("href", "https://github.com/compassionee/ritmo")
        data = sheet.locator("#aboutData")
        expect(data).to_contain_text("Open Food Facts")
        expect(data.get_by_role("link", name="ODbL 1.0")).to_have_attribute("href", "https://opendatacommons.org/licenses/odbl/1-0/")
        expect(data.get_by_role("link", name="DbCL 1.0")).to_be_visible()
        expect(data.get_by_role("link", name="CC0 1.0").first).to_be_visible()
        expect(data.get_by_role("link", name="Licence Ouverte / Etalab 2.0")).to_be_visible()
        expect(data).to_contain_text("2025-11-03")   # Ciqual: source and version date
        expect(data).to_contain_text("2026-04-30")   # USDA Foundation Foods
        expect(data).to_contain_text("2026-10-21")   # the installed Open Food Facts extract
        soft = sheet.locator("#aboutSoft")
        for name in ("ZXing", "AndroidX CameraX", "Gadgetbridge"):
            expect(soft).to_contain_text(name)
        expect(soft).to_contain_text("Apache-2.0")
        for a in sheet.locator("a").all():
            self.assertEqual((a.get_attribute("target"), a.get_attribute("rel")), ("_blank", "noopener noreferrer"))
        self.assertNotIn("[object Object]", self.page.locator("body").text_content())  # every source renders as text
        self.page.click("#aboutClose")
        expect(sheet).to_be_hidden()
        self.page.click("#catalogAbout")
        expect(sheet).to_be_visible()
        self.assertEqual(self.outside, [])


    def test_saved_meal_from_a_diary_meal_with_community_foods(self):
        # "Save as saved meal" on a meal with foods from the database: asked once, then saved as copies
        oats = self.food("Oats", OATS)
        off = lambda q: next(x for x in self.h.catalog_search(q, UI_LANG)["community"] if x["src"] == "off")
        bis, apple = off("biscotti"), off("mela snack")
        self.api("/api/diary", {"date": DAY.isoformat(), "op": "add", "meal": "breakfast", "food": oats, "g": 40})
        for it, g in ((bis, 30), (apple, 100)):
            self.api("/api/diary", {"date": DAY.isoformat(), "op": "add", "meal": "breakfast", "g": g,
                                    "catalog": {"src": it["src"], "ref": it["ref"]}, "lang": UI_LANG})
        self.h._daycache.clear()
        self.page.reload()
        self.cibo()
        self.meal_card(self.L("meal.breakfast")).locator("button", has_text=self.L("nut.save_as_meal")).click()
        rows = self.page.locator("#mealItems .it-row")
        expect(rows).to_have_count(3)
        expect(self.page.locator("#mealItems .it-row.it-cat")).to_have_count(2)
        expect(rows.nth(1).locator(".src-tag")).to_have_text("OFF")
        entries = self.diary()["entries"]
        kcal = sum(e["v"]["kcal"] * e["g"] / 100 for e in entries)
        expect(self.page.locator("#mealPreview")).to_contain_text(self.L("meal.preview", n=3, kcal=kcal))
        self.page.click("#mealSave")
        names = [e["name"] for e in entries[1:]]
        ask = self.page.locator("#mealCatAsk")
        expect(ask).to_be_visible()
        expect(ask).to_contain_text(self.L("meal.catalog_ask", n=2, names=", ".join(names)))
        expect(self.page.locator("#mealErr")).to_be_hidden()
        self.assertNotIn("meals", json.loads((self.state / "foods.json").read_text()))  # nothing created in silence
        self.page.click("#mealCatYes")
        self.toast(self.L("meal.catalog_saved", n=2))
        expect(self.page.locator("#mealSheet")).to_be_hidden()
        data = json.loads((self.state / "foods.json").read_text())
        copies = {f["origin"]["ref"]: f for f in data["foods"].values() if f.get("origin")}
        self.assertEqual(sorted(copies), sorted([bis["ref"], apple["ref"]]))
        self.assertEqual(copies[bis["ref"]]["name"], "Biscotti integrali (Marca)")
        self.assertEqual(copies[bis["ref"]]["barcode"], "8001234567897")
        (meal,) = data["meals"].values()
        self.assertEqual(meal["items"], [[oats, 40], [copies[bis["ref"]]["id"], 30], [copies[apple["ref"]]["id"], 100]])
        # a second saved meal from the same diary meal uses the copies: no question, no new foods
        # (first wait for the reload after the save: the diary must already point to the copies)
        expect(self.page.locator("#foodsCount")).to_contain_text(self.L("food.n_meals", n=1))
        self.meal_card(self.L("meal.breakfast")).locator("button", has_text=self.L("nut.save_as_meal")).click()
        expect(self.page.locator("#mealItems .it-row.it-cat")).to_have_count(0)
        self.page.fill("#mealName", "Colazione 2")
        self.page.click("#mealSave")
        expect(self.page.locator("#mealSheet")).to_be_hidden()
        expect(self.page.locator("#foodsCount")).to_contain_text(self.L("food.n_meals", n=2))
        data = json.loads((self.state / "foods.json").read_text())
        self.assertEqual((len(data["meals"]), sum(1 for f in data["foods"].values() if f.get("origin"))), (2, 2))
        self.assertEqual(self.outside, [])


if __name__ == "__main__":
    unittest.main()
