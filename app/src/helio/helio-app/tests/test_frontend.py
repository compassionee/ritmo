"""Interface checks without a browser (standard library only, they run on the server too).

They check that HTML, JavaScript, CSS, service worker and manifest agree with each other and with
the server API: a missing id, a renamed endpoint or a file missing from the cache shows up here.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_helio import T, APP_DIR, DAY, build_db, load_app

STATIC = APP_DIR / "static"
HTML = (STATIC / "index.html").read_text()
JS = (STATIC / "app.js").read_text()
CSS = (STATIC / "style.css").read_text()
SW = (STATIC / "sw.js").read_text()
SERVER = (APP_DIR / "helio_app.py").read_text()


def html_ids():
    return re.findall(r'\bid="([^"]+)"', HTML)


class HtmlAndScript(unittest.TestCase):
    def test_every_element_used_by_the_script_exists(self):
        used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', JS))
        self.assertTrue(used)
        missing = sorted(used - set(html_ids()))
        self.assertEqual(missing, [], f"id usati in app.js ma assenti in index.html: {missing}")

    def test_ids_are_unique(self):
        ids = html_ids()
        dup = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dup, [])

    def test_every_tab_button_has_a_section(self):
        targets = set(re.findall(r'data-(?:tab|go)="([a-z]+)"', HTML))
        self.assertTrue({"today", "fitness", "sleep", "health"} <= targets)
        for t in targets:
            self.assertIn(f'id="tab-{t}"', HTML, t)
            self.assertRegex(JS, rf'\b{t}\s*:\s*"', f"missing title for tab {t}")

    def test_sheet_buttons_have_their_default_text(self):
        # the button must go back to its original text every time the sheet opens
        for bid, key in (("sheetSave", "common.save"), ("sheetRemove", "ui.sheet.remove_label")):
            self.assertRegex(HTML, rf'id="{bid}" data-i18n="{key}">')
            self.assertIn(f'["{bid}", "{key}"]', JS)

    def test_save_always_releases_the_button(self):
        body = JS[JS.index("async function saveLabel"):JS.index("let sheetW")]
        self.assertIn("finally", body)
        self.assertRegex(body, r"finally\s*\{[^}]*btn\.disabled = false")
        self.assertIn("if (saving) return", body)            # double tap
        post = JS[JS.index("async function postJson"):JS.index("let toastTimer")]
        self.assertIn("AbortController", post)               # no endless waits
        self.assertIn("clearTimeout", post)

    def test_manual_edit_saves_new_before_removing_old(self):
        body = JS[JS.index("async function saveLabel"):JS.index("let sheetW")]
        self.assertLess(body.index("type, day: type"), body.index("type: null, manual: true"))

    def test_hidden_attribute_always_hides(self):
        self.assertRegex(CSS, r"\[hidden\]\s*\{\s*display:\s*none\s*!important")

    def test_script_and_style_are_local(self):
        for tag in re.findall(r"<(?:script|link|img)[^>]+>", HTML):
            self.assertNotRegex(tag, r'(?:src|href)="(?:https?:)?//', f"risorsa esterna: {tag}")
        self.assertNotRegex(JS, r'fetch\(\s*["`]https?://', "the JavaScript calls an external server")
        self.assertNotIn("@import url(http", CSS)

    def test_no_browser_storage_or_dialogs(self):
        for word in ("localStorage", "sessionStorage", "indexedDB", "alert(", "confirm(", "prompt("):
            self.assertNotIn(word, JS, word)

    def test_no_inline_scripts_for_csp(self):
        self.assertNotRegex(HTML, r"<script>(?!\s*</script>)")
        self.assertNotRegex(HTML, r'\son[a-z]+="')       # onclick="..." blocked by the CSP

    def test_css_braces_balanced(self):
        clean = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        self.assertEqual(clean.count("{"), clean.count("}"))

    def test_classes_toggled_by_script_are_styled(self):
        for cls in ("toast", "err", "type-btn", "on", "w-btn", "ghost", "sheet"):
            self.assertRegex(CSS, rf"\.{re.escape(cls)}\b", cls)


class ApiContract(unittest.TestCase):
    """The fields the interface reads really exist in the server's answers."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "gb.db"
        build_db(db)
        cls.h = load_app(db, HELIO_STATE=str(Path(cls.tmp.name) / "state"))
        cls.s = cls.h.summary(DAY)
        cls.t = cls.h.trends(DAY)
        cls.v = cls.h.vitals(DAY)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_endpoints_used_by_the_page_exist(self):
        used = set(re.findall(r'"/api/([a-z]+)', JS)) | set(re.findall(r'`/api/([a-z]+)', JS))
        used.discard("android")  # Android app only: see test_android_endpoints_only_in_app
        self.assertEqual(used, {"summary", "trends", "vitals", "label", "week", "strength", "plan", "weight", "export",
                                "nutrition", "foods", "food", "meal", "diary", "goal", "profile", "settings", "catalog"})
        for ep in used:
            self.assertRegex(SERVER, rf'"/api/{ep}[".]', ep)

    def test_no_function_declared_twice(self):
        """A second declaration with the same name silently replaces the first in the whole page."""
        names = re.findall(r"^(?:async )?function (\w+)\(", JS, re.M)
        self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])

    def test_android_endpoints_only_in_app(self):
        """/api/android/* exists only in the app: on the server the page never calls it."""
        self.assertNotIn("/api/android", SERVER)
        self.assertIn("const ANDROID = window.HELIO_ANDROID === true;", JS)
        for name in ("loadBand", "bandAction"):
            body = JS[JS.index(f"function {name}"):]
            body = body[:body.index("\n}\n")]
            self.assertIn("/api/android/", body, name)
        load_band = JS[JS.index("async function loadBand"):]
        self.assertTrue(load_band.split("\n")[1].strip() == "if (!ANDROID) return;")
        # bandAction starts only from the menu, which opens only with ANDROID
        self.assertIn('addEventListener("click", () => (ANDROID ? openBand() : show("profile")))', JS)

    def test_pull_to_sync_only_in_app(self):
        """Pull down to sync: installed only with ANDROID, goes through the controlled action."""
        self.assertIn("\nif (ANDROID) pullInstall();\n", JS)
        self.assertEqual(JS.count("pullInstall()"), 2)  # definition and the only call
        block = JS[JS.index("// pull:begin"):JS.index("// pull:end")]
        self.assertIn('postJson("/api/android/action", { action: "pull" })', block)
        self.assertNotIn('action: "sync"', block)  # the decision to sync belongs to Python
        self.assertIn("if (!ANDROID || pullBusy", block)
        self.assertIn('id="pullBox"', HTML)
        self.assertIn(".pull ", CSS)

    def test_view_and_back_only_in_app(self):
        """Saved view and Back button: only in the app, where Java uses them; nothing changes on the server."""
        self.assertIn("\n  const restoreY = ANDROID ? viewInstall() : null;\n", JS)
        block = JS[JS.index("// view:begin"):JS.index("// view:end")]
        self.assertIn('if (!ANDROID || typeof window.HELIO_SAVE_VIEW !== "function") return;', block)
        self.assertIn("window.helioBack = helioBack;", block)
        self.assertNotIn("fetch(", block)  # the view does not go through the API
        self.assertNotIn("localStorage", JS)

    def test_summary_fields_read_by_the_page(self):
        fields = set(re.findall(r"\b(?:d|S)\.([a-z_]+)\b", JS))
        missing = sorted(f for f in fields if f not in self.s)
        self.assertEqual(missing, [], f"fields read by the interface but missing from the summary: {missing}")

    def test_trends_fields_read_by_the_page(self):
        fields = set(re.findall(r"\bt\.(days|goals|[a-z_]+_week|weeks)\b", JS))
        for f in fields:
            self.assertIn(f, self.t, f)

    def test_training_fields_read_by_the_page(self):
        w = self.s["trainings"][0]
        body = JS[JS.index("function renderFitness"):JS.index("const saveTimeoutMs")] + JS[JS.index("function fillWorkout"):JS.index("/* ================= SLEEP")]
        fields = set(re.findall(r"\bw\.([a-z_]+)\b", body))
        missing = sorted(f for f in fields if f not in w and f not in self.s["workouts"][0])
        self.assertEqual(missing, [], missing)
        for f in ("type", "day", "name", "confidence", "reason"):
            self.assertIn(f, w["suggestion"])

    def test_activity_types_have_icons_and_colours(self):
        keys = [t["key"] for t in self.s["activity_types"]]
        self.assertEqual(len(keys), len(set(keys)))
        icons = JS[JS.index("const TYPE_ICONS"):JS.index("const TYPE_TINT")]
        tints = re.search(r"const TYPE_TINT = \{(.*?)\};", JS).group(1)
        for k in keys:
            self.assertRegex(icons, rf"\n\s*{k}: \"M", f"manca l'icona per {k}")
            m = re.search(rf"\b{k}: \"([a-z]*)\"", tints)
            self.assertIsNotNone(m, f"missing colour for {k}")
            if m.group(1):
                self.assertRegex(CSS, rf"\.w-ico\.{m.group(1)}\b", f"colour {m.group(1)} not defined in the CSS")


def js_function(name):
    """Body of an app.js function (up to the closing brace in column 0)."""
    start = JS.index(f"function {name}(")
    return JS[start:JS.index("\n}\n", start)]


def js_fields(code, var):
    # not the translation keys ("food.edit"): variables only
    return set(re.findall(rf"(?<![\"'\w-]){var}\.([a-z_0-9]+)\b", code))


class NutritionContract(unittest.TestCase):
    """The fields the Food tab reads really exist in /api/nutrition and /api/foods."""

    @classmethod
    def setUpClass(cls):
        from datetime import timedelta
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "gb.db"
        build_db(db)
        h = cls.h = load_app(db, HELIO_STATE=str(Path(cls.tmp.name) / "state"))
        per = {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10, "sat": 1.3, "salt": 0.02}
        oats = h.save_food({"food": {"name": "Oats", "per100": per, "portions": [["1 cup", 40]]}})["id"]
        milk = h.save_food({"food": {"name": "Latte", "per100": {"kcal": 46, "protein": 3.4, "carbs": 4.9, "fat": 1.5, "fiber": 0}}})
        cls.saved = milk
        rec = h.save_food({"food": {"kind": "recipe", "name": "Porridge", "items": [[oats, 50], [milk["id"], 200]],
                                    "cooked_g": 260}})["id"]
        meal = h.save_meal({"meal": {"name": "Usual breakfast", "items": [[oats, 60], [rec, 100]]}})["id"]
        # the shipped generic catalog: a copy among my foods and a community food in the diary
        cls.cat_search = h.catalog_search("mela", "it")
        cls.cat_item = cls.cat_search["community"][0]
        cls.cat_barcode = h.catalog_barcode("20570910", "it")
        it = cls.cat_item
        h.save_food({"food": {"name": "Mela copiata", "per100": it["per100"], "origin": {"src": it["src"], "ref": it["ref"]}}})
        for i in range(4):
            d = (DAY - timedelta(days=i)).isoformat()
            h.diary_op({"date": d, "op": "add_meal", "meal": "breakfast", "template": meal})
            h.diary_op({"date": d, "op": "add", "meal": "breakfast", "g": 150, "catalog": {"src": it["src"], "ref": it["ref"]}})
            h.diary_op({"date": d, "op": "complete", "complete": True})
        cls.cat_info = h.catalog_info()
        h.save_weight(DAY - timedelta(days=1), 81)
        cls.n = h.nutrition_day(DAY)
        cls.f = h.foods_payload()
        cls.recipe = next(x for x in cls.f["foods"] if x["kind"] == "recipe")
        cls.food_ = next(x for x in cls.f["foods"] if x["kind"] == "food" and x.get("origin"))
        cls.check = cls.n["week"]["checks"][0]
        t0 = h.midnight(DAY)
        tr = lambda a, b, typ: {"start": t0 + a * 3600, "end": t0 + b * 3600, "name": typ, "duration_min": (b - a) * 60,
                                "label": {"type": typ}, "suggestion": {"type": typ}}
        hist = [{"date": DAY.isoformat(), "has_data": True, "worn_min": 1440, "trainings": [tr(7, 8, "run"), tr(12, 13, "strength")],
                 "kcal": {"total": 2500, "range": [2400, 2600], "active": 700, "active_range": [600, 800], "partial": True},
                 "profile": {"bmr": 1800}}]
        cls.rc = h.nutrition_analysis(None, DAY, {"weight": 74, "weight_src": "log"}, hist)["recovery"]

    @classmethod
    def tearDownClass(cls):
        import os
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def assert_fields(self, code, var, obj):
        fields = js_fields(code, var)
        self.assertTrue(fields, var)
        missing = sorted(f for f in fields if f not in obj)
        self.assertEqual(missing, [], f"{var}.: fields read by the interface but missing: {missing}")

    def test_day_and_week(self):
        body = js_function("renderFood")
        self.assert_fields(body, "n", self.n)
        self.assert_fields(body, "tot", self.n["totals"])
        self.assert_fields(body, "ex", self.n["expenditure"])
        self.assert_fields(body, "bal", self.n["balance"])
        self.assert_fields(body, "wk", self.n["week"])
        self.assert_fields(body, "wc", self.n["weight_check"])
        self.assert_fields(body, "a", self.n["athlete"])
        self.assert_fields(js_function("renderNutChecks"), "c", self.check)
        self.assert_fields(js_function("nutWeekChart"), "x", self.n["days"][0])
        self.assertIn("shares", self.n)

    def test_recovery(self):
        body = js_function("renderFood")
        self.assertIsNotNone(self.rc)
        self.assert_fields(body, "rc", self.rc)
        self.assert_fields(body, "p", self.rc["pairs"][0])

    def test_meals_and_entries(self):
        meal = self.n["meals"][0]
        body = js_function("renderMeals")
        self.assert_fields(body, "m", meal)
        self.assert_fields(body, "e", meal["entries"][0])
        self.assert_fields(js_function("openEntry"), "entry", meal["entries"][0])
        self.assert_fields(js_function("openMealEditor"), "fromDiary", meal)
        self.assert_fields(js_function("openMealEditor"), "e", meal["entries"][0])

    def test_catalog(self):
        self.assert_fields(js_function("drawCatResults"), "it", self.cat_item)
        self.assert_fields(js_function("drawCatResults"), "r", {**self.cat_search, **self.cat_barcode})
        self.assert_fields(js_function("lookupScanned"), "r", self.cat_barcode)
        self.assert_fields(js_function("openEntry"), "cat", self.cat_item)
        self.assert_fields(js_function("openFoodEditor"), "cat", self.cat_item)
        self.assert_fields(js_function("catDisplayName"), "it", self.cat_item)
        self.assert_fields(js_function("renderCatalog"), "c", self.cat_info)
        m = self.cat_info["installed"]["generic"]
        self.assert_fields(js_function("renderCatalog"), "m", m)
        self.assert_fields(js_function("renderCatalog"), "x", m["sources"][0])
        entry = next(e for x in self.n["meals"] for e in x["entries"] if e["src"]["src"] != "user")
        self.assert_fields(js_function("renderMeals"), "e", entry)
        self.assertEqual(entry["src"]["src"], self.cat_item["src"])

    def test_foods(self):
        for name in ("renderFoodsList", "drawPick"):
            body = js_function(name)
            self.assert_fields(body, "f", {**self.food_, **self.recipe})
            self.assert_fields(body, "m", self.f["meals"][0])
            self.assert_fields(body, "i", self.f["meals"][0]["items"][0])
        self.assert_fields(js_function("renderFoodsList"), "F", self.f)
        self.assert_fields(js_function("openFoodEditor"), "food", self.food_)
        self.assert_fields(js_function("openRecipeEditor"), "rec", self.recipe)
        self.assert_fields(js_function("openMealEditor"), "m", self.f["meals"][0])
        self.assert_fields(js_function("openEntry"), "food", self.food_)
        self.assert_fields(js_function("foodCheck"), "E", self.f["factors"])
        self.assertEqual(set(js_fields(JS, "res")) - {"updated", "id"}, set())

    def test_label_check_matches_the_server(self):
        # same rounding and same factors as the server check (helio_science.label_energy_check)
        import helio_science as sci
        self.assertIn("const rt = (v) => (v >= 10 ? 0.5 : v >= 0.5 ? 0.05 : 0.5);", JS)
        self.assertEqual((sci.rounding_tol(10), sci.rounding_tol(0.5), sci.rounding_tol(0.4)), (0.5, 0.05, 0.5))
        self.assertIn("FOODS.factors", JS)
        self.assertEqual(self.f["factors"], sci.ATWATER)

    def test_food_tab_is_wired(self):
        self.assertIn('data-tab="food"', HTML)
        self.assertRegex(CSS, r"grid-template-columns: repeat\(5, 1fr\)")      # five tabs in the bar
        self.assertIn('nutrition: "food"', JS)                              # Today's comments lead to the tab
        for sheet in ("pickSheet", "entrySheet", "foodSheet", "recipeSheet", "mealSheet"):
            self.assertIn(f'["{sheet}", "', JS)                                # they close like the other sheets
        for dl in ("foodNames", "ingredientNames"):                              # name suggestions in the ingredients
            self.assertIn(f'<datalist id="{dl}">', HTML)
            self.assertIn(f'"{dl}"', JS)

    def test_swipe_is_ignored_inside_sheets_and_chips(self):
        body = JS[JS.index('document.addEventListener("touchstart"'):JS.index('document.addEventListener("touchend"')]
        self.assertIn('.sheet")].some((x) => !x.hidden)', body)
        self.assertIn(".chips", body)


class ServiceWorker(unittest.TestCase):
    def test_shell_files_exist(self):
        shell = json.loads(re.search(r"const SHELL = (\[.*?\]);", SW).group(1).replace("'", '"'))
        for p in shell:
            f = STATIC / ("index.html" if p == "/" else p.lstrip("/"))
            self.assertTrue(f.is_file(), p)

    def test_every_static_file_is_served_and_cached(self):
        # a new file in static/ (for example a language) must be served by the server and cached
        served = set(re.findall(r'^    "(/[^"]*)": \("', SERVER, re.M))
        shell = set(json.loads(re.search(r"const SHELL = (\[.*?\]);", SW).group(1)))
        for f in STATIC.rglob("*"):
            if f.is_file() and f.name != "sw.js":
                path = "/" + f.relative_to(STATIC).as_posix()
                self.assertIn(path, served, path)
                self.assertIn("/" if path == "/index.html" else path, shell, path)

    def test_never_caches_data_or_posts(self):
        self.assertIn('url.pathname.startsWith("/api/")', SW)
        self.assertIn('e.request.method !== "GET"', SW)

    def test_cache_name_and_old_caches_removed(self):
        self.assertRegex(SW, r'const CACHE = "helio-v\d+"')
        self.assertIn("caches.delete", SW)

    def test_manifest(self):
        m = json.loads((STATIC / "manifest.json").read_text())
        for k in ("name", "start_url", "display", "icons"):
            self.assertIn(k, m)
        for icon in m["icons"]:
            self.assertTrue((STATIC / icon["src"].lstrip("/")).is_file(), icon["src"])


class PlanFile(unittest.TestCase):
    def test_default_plan_is_valid(self):
        plan = json.loads((Path(__file__).resolve().parent / "plan-sample.json").read_text())
        self.assertEqual(sorted(plan), [str(i) for i in range(1, 8)])
        for k, v in plan.items():
            self.assertIn("text", v)
            for ex in v.get("exercises", []):
                self.assertEqual(len(ex), 2)
                self.assertTrue(ex[0].strip())

    def test_default_load_on_strength_days(self):
        plan = json.loads((Path(__file__).resolve().parent / "plan-sample.json").read_text())
        for k, v in plan.items():
            self.assertEqual(v.get("load_kg"), 10 if "strength" in v["types"] else None, k)

    def test_sample_plan_covers_the_week(self):
        """Sample plan: a rest day, three strength days (1-3) and light activities."""
        plan = json.loads((Path(__file__).resolve().parent / "plan-sample.json").read_text())
        self.assertEqual(plan["1"]["types"], [])
        self.assertEqual(sorted(v["day"] for v in plan.values() if "strength" in v["types"]), [1, 2, 3])
        self.assertTrue(all(len(v["exercises"]) >= 3 for v in plan.values() if "strength" in v["types"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
