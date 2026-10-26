"""Internal codes were Italian up to versionCode 259 (activity types, meals, goals, statuses...). Data written by those
versions, backups exported by them and the view saved by the Android app must keep working: old codes are converted
when read (LEGACY_CODES in helio_app.py) and only English codes are written.
"""
import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_helio import APP_DIR, DAY, build_db, load_app  # noqa: I001  (sets sys.path for the app modules)
import helio_transfer as ht  # noqa: E402

# a state folder as saved by versionCode 259 (legacy Italian codes)
OLD_STATE = {
    "labels.json": {"1773140400": {"start": 1773140400, "end": 1773142200, "type": "forza", "day": 2, "load_kg": 15,  # legacy
                                   "features": {}},
                    "1773147600": {"start": 1773147600, "end": 1773169200, "type": "lavoro", "manual": True}},  # legacy
    "plan.json": {"1": {"text": "Rest", "types": [], "exercises": []},
                  "2": {"text": "Run", "types": ["corsa", "corda"], "exercises": []},  # legacy
                  "4": {"text": "Strength", "types": ["forza"], "day": 1, "exercises": [["Squat", "4x10"]]}},  # legacy
    "foods.json": {"foods": {"f1": {"kind": "alimento", "name": "Oats",  # legacy
                                    "per100": {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10}},
                             "r1": {"kind": "ricetta", "name": "Porridge", "items": [["f1", 50]], "cooked_g": 200}},  # legacy
                   "meals": {"m1": {"name": "Usual breakfast", "meal": "colazione", "items": [["f1", 60]]}}},  # legacy
    "nutrition.json": {"goal": "mantenere"},  # legacy
}


def old_diary():
    v = {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10}
    return {DAY.isoformat(): {"complete": True, "entries": [
        {"id": "e1", "meal": "pranzo", "kind": "alimento", "food": "f1", "name": "Oats", "g": 100,  # legacy
         "v": v, "lo": v, "hi": v, "added": 1}]}}


def all_codes(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("type", "kind", "meal", "goal") and isinstance(v, str):
                out.add(v)
            elif k == "types" and isinstance(v, list):
                out.update(x for x in v if isinstance(x, str))
            else:
                all_codes(v, out)
    elif isinstance(obj, list):
        for v in obj:
            all_codes(v, out)
    return out


class OldData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        build_db(d / "gb.db")
        self.state = d / "state"
        self.state.mkdir()
        for name, data in OLD_STATE.items():
            (self.state / name).write_text(json.dumps(data))
        (self.state / f"diary-{DAY:%Y-%m}.json").write_text(json.dumps(old_diary()))
        self.h = load_app(d / "gb.db", HELIO_STATE=str(self.state))

    def tearDown(self):
        self.tmp.cleanup()

    def files(self):
        return {p.name: json.loads(p.read_text()) for p in self.state.glob("*.json")}

    def test_old_codes_are_converted_when_read(self):
        legacy = set(self.h.LEGACY_CODES)
        self.assertEqual(self.h.labels_load()["1773140400"]["type"], "strength")
        self.assertEqual(self.h.plan_for(DAY)["types"], ["run", "rope"])          # DAY is a Tuesday
        foods, meals = self.h.foods_load()
        self.assertEqual((foods["f1"]["kind"], foods["r1"]["kind"], meals["m1"]["meal"]), ("food", "recipe", "breakfast"))
        self.assertEqual(self.h.nutrition_settings()["goal"], "maintain")
        entry = self.h.diary_days(DAY, DAY)[DAY.isoformat()]["entries"][0]
        self.assertEqual((entry["meal"], entry["kind"]), ("lunch", "food"))
        # the files themselves now hold only English codes
        codes = all_codes(self.files(), set())
        self.assertTrue(codes)
        self.assertEqual(codes & legacy, set())

    def test_the_whole_api_works_on_old_data(self):
        s = self.h.summary(DAY)
        self.assertEqual(s["plan"]["types"], ["run", "rope"])
        n = self.h.nutrition_day(DAY)
        self.assertEqual(n["goal"], "maintain")
        self.assertEqual([m["key"] for m in n["meals"] if m["entries"]], ["lunch"])
        self.assertEqual(self.h.strength_day(DAY)["strength_day_numbers"], [1])
        for res in (s, self.h.trends(DAY), self.h.week_summary(DAY), n, self.h.foods_payload(), self.h.plan_payload()):
            self.assertEqual(all_codes(res, set()) & set(self.h.LEGACY_CODES), set())

    def test_old_codes_in_requests_are_accepted_and_saved_in_english(self):
        w = self.h.summary(DAY)["trainings"][0]
        self.h.save_label(w["start"], w["end"], "nuoto", None, None)                # legacy
        self.assertEqual(self.h.labels_load()[str(w["start"])]["type"], "swim")
        self.h.save_goal({"goal": "perdere"})                                       # legacy
        self.assertEqual(json.loads((self.state / "nutrition.json").read_text())["goal"], "lose_fat")
        self.h.diary_op({"date": DAY.isoformat(), "op": "add", "meal": "cena", "food": "f1", "g": 50})  # legacy
        self.assertEqual(self.h.diary_days(DAY, DAY)[DAY.isoformat()]["entries"][-1]["meal"], "dinner")
        plan = self.h.validate_plan({"3": {"text": "x", "types": ["camminata"]}})  # legacy
        self.assertEqual(plan["3"]["types"], ["walk"])
        self.assertEqual(all_codes(self.files(), set()) & set(self.h.LEGACY_CODES), set())

    def test_import_of_an_old_backup(self):
        """A .tgz exported by versionCode 259 (Italian codes) imports and reads as English."""
        old = Path(self.tmp.name) / "old-state"     # untouched copy, as on the server or phone of version 259
        old.mkdir()
        for name, data in OLD_STATE.items():
            (old / name).write_text(json.dumps(data))
        (old / f"diary-{DAY:%Y-%m}.json").write_text(json.dumps(old_diary()))
        archive = ht.export_bytes(str(old))
        target = Path(self.tmp.name) / "imported"
        ht.import_bytes(archive, str(target))
        self.assertIn("forza", (target / "labels.json").read_text())                # legacy: the archive is untouched
        h = load_app(Path(self.tmp.name) / "gb.db", HELIO_STATE=str(target))
        self.assertEqual(h.nutrition_settings()["goal"], "maintain")
        self.assertEqual(h.labels_load()["1773140400"]["type"], "strength")
        h.summary(DAY)
        h.nutrition_day(DAY)
        codes = all_codes({p.name: json.loads(p.read_text()) for p in target.glob("*.json")}, set())
        self.assertEqual(codes & set(h.LEGACY_CODES), set())

    def test_configuration_plan_file_is_read_but_not_rewritten(self):
        conf = Path(self.tmp.name) / "plan.json"
        conf.write_text(json.dumps({"2": {"text": "x", "types": ["nuoto"]}}))     # legacy
        (self.state / "plan.json").unlink()
        h = load_app(Path(self.tmp.name) / "gb.db", HELIO_STATE=str(self.state), HELIO_PLAN=str(conf))
        self.assertEqual(h.plan_for(DAY)["types"], ["swim"])
        self.assertIn("nuoto", conf.read_text())                                   # legacy


# ---------------------------------------------------------------- no Italian code outside the table
def legacy_codes():
    tree = ast.parse((APP_DIR / "helio_app.py").read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "LEGACY_CODES" for t in n.targets):
            return {k.value for k in n.value.keys}
    raise AssertionError("LEGACY_CODES not found")


class NoLegacyCodeOutsideTheTable(unittest.TestCase):
    """Old codes may appear only in the conversion tables (LEGACY_CODES, LEGACY_TABS), in keyword lists matched
    against text typed by the user (lines marked "keywords") and in tests of old data (lines marked "legacy")."""

    def test_python(self):
        legacy = legacy_codes()
        for name in ("helio_app.py", "helio_science.py", "helio_transfer.py"):
            path = APP_DIR / name
            src = path.read_text()
            lines = src.split("\n")
            tree = ast.parse(src)
            table = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Assign) and any(getattr(t, "id", "").startswith("LEGACY_") for t in n.targets):
                    table.update(range(n.lineno, n.end_lineno + 1))
            found = []
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in legacy:
                    line = lines[n.lineno - 1]
                    if n.lineno not in table and "keywords" not in line:
                        found.append(f"{name}:{n.lineno}: {line.strip()[:90]}")
            self.assertEqual(found, [], "old codes outside the conversion table")

    def test_page(self):
        legacy = legacy_codes()
        pat = re.compile(r"""(?:["'`]|\b(?:st|a|tab|coi)-|data-(?:tab|k|goal|meal)=")(%s)(?:["'`]|\b)"""
                         % "|".join(map(re.escape, sorted(legacy, key=len, reverse=True))))
        found = []
        for name in ("app.js", "index.html", "style.css"):
            for i, line in enumerate((APP_DIR / "static" / name).read_text().split("\n"), 1):
                if "LEGACY_TABS" in line:
                    continue
                for m in pat.finditer(line):
                    found.append(f"{name}:{i}: {m.group(0)}")
            # object keys in JavaScript (for example { fatto: "wk.done" })
            if name == "app.js":
                for i, line in enumerate((APP_DIR / "static" / name).read_text().split("\n"), 1):
                    if "LEGACY_TABS" in line:
                        continue
                    for m in re.finditer(r"(?:^|[{,])\s*(%s)\s*:" % "|".join(map(re.escape, legacy)), line):
                        found.append(f"{name}:{i}: {m.group(1)}:")
        self.assertEqual(found, [])

    def test_android_flavor(self):
        flavor = APP_DIR.parent
        if not (flavor / "python" / "helio_android.py").is_file():
            self.skipTest("only in the Gadgetbridge flavor")
        legacy = legacy_codes()
        pat = re.compile(r"""["'`](%s)["'`]""" % "|".join(map(re.escape, legacy)))
        files = [*flavor.glob("python/*.py"), *flavor.glob("python-tests/*.py"), *flavor.glob("js-tests/*.mjs"),
                 *flavor.glob("assets/**/*.js"), *flavor.glob("java/**/*.java"), *(flavor.parent / "testHelio").glob("**/*.java")]
        found = [f"{p.name}:{i}" for p in files for i, line in enumerate(p.read_text().split("\n"), 1)
                 if pat.search(line) and "legacy" not in line]
        self.assertEqual(found, [])

    def test_catalog_keys(self):
        legacy = legacy_codes() | {"forza_day"}  # lang: it
        for lang in ("en", "it"):
            keys = json.loads((APP_DIR / "static" / "i18n" / f"{lang}.json").read_text())
            bad = sorted(k for k in keys if set(k.split(".")) & legacy)
            self.assertEqual(bad, [], lang)

    def test_table_is_complete_and_unambiguous(self):
        h = load_app(Path(tempfile.gettempdir()) / "none.db")
        table = h.LEGACY_CODES
        self.assertEqual(set(table), legacy_codes())
        self.assertTrue(all(re.fullmatch(r"[a-z_]+", v) for v in table.values()))
        self.assertFalse(set(table) & set(table.values()))                  # a new code is never an old one
        for t in h.ACTIVITY_TYPES:
            self.assertNotIn(t, table)
        self.assertEqual(set(h.ACTIVITY_TYPES) - set(table.values()), set())
        self.assertTrue(set(h.MEALS) <= set(table.values()) and set(h.GOALS) <= set(table.values()))
        self.assertEqual(h.code("forza"), "strength")                       # legacy
        self.assertEqual(h.code("strength"), "strength")


if __name__ == "__main__":
    unittest.main(verbosity=2)
