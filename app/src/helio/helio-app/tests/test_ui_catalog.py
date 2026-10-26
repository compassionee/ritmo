"""Browser tests of the food catalog: search in the "Add" sheet (my foods first, then the community with the
source of every value), no request leaves the page's own server while searching, community foods in the diary
without saving them, "Save to my foods", typed barcodes, the Food database card in Profile. Like test_ui.py:
Chromium with Playwright, skipped on the server."""
import json
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
        for q in ("me", "mel", "mela", "biscotti", "caffe", "8001234567890", "zzzz"):
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
        self.search("8001234567890")
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
                         ("off", "8001234567890", "8001234567890", 8.0))
        self.page.click("#entryClose")
        # my copy now comes first for the same barcode, the community product stays
        self.open_pick("dinner")
        self.search("8001234567890")
        expect(self.page.locator("#pickCat .pick-row")).to_have_count(2)
        expect(self.page.locator("#pickCat .pick-row").first).not_to_contain_text("OFF")

    def test_scanned_code_with_one_product_shows_it_before_adding(self):
        """In the app the camera sends the digits back with window.helioScanned; in the browser the button is hidden."""
        self.open_pick()
        expect(self.page.locator("#pickScan")).to_be_hidden()
        self.requests.clear()
        self.page.evaluate("window.helioScanned({code: '8001234567890'})")
        expect(self.page.locator("#entrySheet")).to_be_visible()  # name and brand first, nothing added yet
        expect(self.page.locator("#entryTitle")).to_have_text("Biscotti integrali (Marca)")
        expect(self.page.locator("#entrySub")).to_contain_text("Marca")
        self.assertFalse(list(self.state.glob("diary-*.json")))
        self.assertEqual({urlparse(u).path for u in self.requests}, {"/api/catalog/barcode"})
        self.page.evaluate("window.helioScanned({error: 'permission'})")
        self.toast(self.Lpart("cat.scan_permission", 0))
        self.assertEqual(self.outside, [])

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
        self.page.evaluate("window.helioScanned({code: '4000000000000'})")
        self.page.locator("#pickCat button.add-btn").click()
        for sel, v in (("#foodName", "Torta della nonna"), ("#fKcal", "350"), ("#fProtein", "6"), ("#fCarbs", "45"),
                       ("#fFat", "16"), ("#fFiber", "1")):
            self.page.fill(sel, v)
        self.page.click("#foodSave")
        expect(self.page.locator("#entrySheet")).to_be_visible()
        (f,) = json.loads((self.state / "foods.json").read_text())["foods"].values()
        self.assertEqual(f["barcode"], "4000000000000")

    def test_unknown_barcode(self):
        self.open_pick()
        self.search("4000000000000")
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
        card.locator("summary").click()
        expect(self.page.locator("#catalogUrl")).to_have_value("")
        expect(self.page.locator("#catalogUrlUpdate")).to_be_disabled()
        self.page.fill("#catalogUrl", "http://example.org/x.tar")
        self.page.click("#catalogUrlSave")
        expect(self.page.locator("#catalogErr")).to_contain_text(self.Lpart("cat.e.url", 0))
        self.page.fill("#catalogUrl", "https://example.org/x.tar")
        self.page.click("#catalogUrlSave")
        self.toast(self.L("cat.url_saved"))
        expect(self.page.locator("#catalogUrlUpdate")).to_be_enabled()
        self.page.fill("#catalogUrl", "")
        self.page.click("#catalogUrlSave")
        self.toast(self.L("cat.url_saved"))
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


if __name__ == "__main__":
    unittest.main()
