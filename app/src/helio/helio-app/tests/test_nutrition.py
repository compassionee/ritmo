"""Nutrition tests: formulas (EU factors and tolerances, Hall 2008, weight trend), foods, recipes,
saved meals, diary, analysis of deficiencies and excesses with the thresholds of the sources, API and export.

The thresholds checked here come from the sources in SOURCES: EFSA (protein 0.83 g/kg, carbohydrates 45-60%, fat 20-35%,
fibre 25 g), WHO 2023 (fat within 30%, saturated fat below 10%, fibre at least 25 g), WHO 2012 (salt below 5 g),
Murphy & Koehler 2022 (deficit above 500 kcal), Refalo 2025 and Morton 2018 (flagged), Craven 2021 (recovery within 8 hours),
Hall 2008 (39.5 and 7.6 MJ/kg), ACSM 2016 (flagged reference only).
"""
import json
import math
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from test_helio import T, DAY, TZ, build_db, load_app

import helio_science as sci

OATS = {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7.0, "fiber": 10.0, "sat": 1.3, "salt": 0.02}
PASTA = {"kcal": 359, "protein": 12.5, "carbs": 71.2, "fat": 2.0, "fiber": 3.0, "sat": 0.5, "salt": 0.01}  # a real label
TOMATO = {"kcal": 34, "protein": 1.3, "carbs": 5.5, "fat": 0.2, "fiber": 1.5}                            # no saturated fat or salt
OIL = {"kcal": 899, "protein": 0, "carbs": 0, "fat": 99.9, "fiber": 0, "sat": 14.0, "salt": 0}


def entry(v, g, name="x", lo=None, hi=None):
    """Diary entry built by hand (for the threshold tests)."""
    lo_, hi_ = sci.food_range(v)
    return {"id": name, "meal": "lunch", "name": name, "g": g, "v": v, "lo": lo if lo is not None else lo_,
            "hi": hi if hi is not None else hi_}


def exact(v, g, name="x"):
    """Entry without uncertainty: minimum and maximum equal to the value."""
    return entry(v, g, name, lo=dict(v), hi=dict(v))


class Science(unittest.TestCase):
    def test_eu_energy_factors(self):
        self.assertEqual(sci.ATWATER, {"carbs": 4.0, "protein": 4.0, "fat": 9.0, "fiber": 2.0, "polyols": 2.4, "alcohol": 7.0})
        self.assertAlmostEqual(sci.atwater_kcal({"carbs": 10, "protein": 10, "fat": 10, "fiber": 10}), 190)
        # polyols are part of carbohydrates and are worth 2.4 kcal/g
        self.assertAlmostEqual(sci.atwater_kcal({"carbs": 10, "polyols": 5, "protein": 0, "fat": 0, "fiber": 0}), 5 * 4 + 5 * 2.4)
        self.assertAlmostEqual(sci.atwater_kcal({"carbs": 0, "protein": 0, "fat": 0, "fiber": 0, "alcohol": 10}), 70)

    def test_label_tolerances(self):
        t = sci.label_tolerance
        for v, tol in ((0, 2), (5, 2), (10, 2), (25, 5), (40, 8), (60, 8)):
            for k in ("carbs", "protein", "fiber"):
                self.assertAlmostEqual(t(k, v), tol, msg=(k, v))
        for v, tol in ((5, 1.5), (9.9, 1.5), (20, 4), (50, 8)):
            self.assertAlmostEqual(t("fat", v), tol, msg=v)
        for v, tol in ((3, 0.8), (4, 0.8), (10, 2)):
            self.assertAlmostEqual(t("sat", v), tol, msg=v)
        for v, tol in ((1, 0.375), (2, 0.4)):
            self.assertAlmostEqual(t("salt", v), tol, msg=v)
        self.assertEqual(t("alcohol", 5), 0)

    def test_food_range(self):
        lo, hi = sci.food_range(OATS)
        self.assertAlmostEqual(lo["carbs"], 58.7 - 8)          # above 40 g: ±8 g
        self.assertAlmostEqual(hi["protein"], 13.5 * 1.2)      # 10-40 g: ±20%
        self.assertAlmostEqual(lo["fat"], 5.5)                 # below 10 g: ±1.5 g
        self.assertAlmostEqual(lo["salt"], 0)                  # never below zero
        e = sci.atwater_kcal(OATS)
        self.assertAlmostEqual(lo["kcal"], 372 * sci.atwater_kcal(lo) / e)
        self.assertAlmostEqual(hi["kcal"], 372 * sci.atwater_kcal(hi) / e)
        self.assertLess(lo["kcal"], 372)
        self.assertGreater(hi["kcal"], 372)
        lo, hi = sci.food_range(OIL)
        self.assertEqual(hi["fat"], 100)                       # never above 100 g per 100 g
        lo, hi = sci.food_range({"kcal": 0, "protein": 0, "carbs": 0, "fat": 0, "fiber": 0})
        self.assertEqual((lo["kcal"], hi["kcal"]), (0, 0))     # water: no invented kcal

    def test_label_energy_check(self):
        ok = sci.label_energy_check(PASTA)                     # 359 kcal declared, 358.8 from the nutrients
        self.assertTrue(ok["ok"])
        self.assertAlmostEqual(ok["computed"], 358.8)
        typo = sci.label_energy_check({**PASTA, "kcal": 35.9})  # misplaced decimal comma
        self.assertFalse(typo["ok"])
        old = sci.label_energy_check({"kcal": 353, "protein": 12.5, "carbs": 71.2, "fat": 2.0, "fiber": 3.0})
        self.assertFalse(old["ok"])
        self.assertTrue(old["ok_without_fiber"])              # label that does not count fibre
        # tolerance = rounding (to the gram from 10 g, to the tenth below) + 0.5 kcal
        self.assertEqual(ok["tol"], round(0.5 + 4 * 0.5 + 4 * 0.5 + 9 * 0.05 + 2 * 0.05, 1))
        self.assertEqual((sci.rounding_tol(10), sci.rounding_tol(9.9), sci.rounding_tol(0.5), sci.rounding_tol(0.3)),
                         (0.5, 0.05, 0.05, 0.5))

    def test_judge(self):
        j = sci.judge
        self.assertEqual(j(30, 28, 32, minimum=25), "ok")
        self.assertEqual(j(20, 18, 24, minimum=25), "certain")
        self.assertEqual(j(20, 15, 27, minimum=25), "probable")
        self.assertEqual(j(6, 5.5, 7, maximum=5), "certain")
        self.assertEqual(j(6, 4, 7, maximum=5), "probable")
        self.assertEqual(j(4, 3, 6, maximum=5), "ok")           # the central value respects the threshold

    def test_energy_shares(self):
        v = {"carbs": 50, "protein": 25, "fat": 10, "fiber": 0, "sat": 4}
        sh = sci.energy_shares(v, dict(v), dict(v))
        e = 200 + 100 + 90
        for k, en in (("carbs", 200), ("protein", 100), ("fat", 90)):
            self.assertAlmostEqual(sh[k]["value"], 100 * en / e)
            self.assertAlmostEqual(sh[k]["lo"], sh[k]["value"])
            self.assertAlmostEqual(sh[k]["hi"], sh[k]["value"])
        self.assertAlmostEqual(sh["sat"]["value"], 100 * 36 / e)
        lo, hi = sci.food_range({**v, "kcal": 390})
        sh = sci.energy_shares(v, lo, hi)
        for k in ("carbs", "protein", "fat", "sat"):
            self.assertLessEqual(sh[k]["lo"], sh[k]["value"])
            self.assertGreaterEqual(sh[k]["hi"], sh[k]["value"])
        self.assertIsNone(sci.energy_shares({"carbs": 0, "protein": 0, "fat": 0, "fiber": 0}, {}, {}))

    def test_t_values_and_trend(self):
        self.assertEqual((sci.t975(1), sci.t975(2), sci.t975(30)), (12.706, 4.303, 2.042))
        self.assertEqual(sci.t975(35), 2.042)                  # beyond the table: conservative value
        self.assertEqual(sci.t975(1000), 1.980)
        self.assertIsNone(sci.t975(0))
        tr = sci.linear_trend([0, 7, 14, 21], [74, 73.5, 73, 72.5])
        self.assertAlmostEqual(tr["slope"], -0.5 / 7)
        self.assertAlmostEqual(tr["lo"], tr["hi"])             # perfect line: no uncertainty
        xs, ys = [0, 1, 2, 3, 4], [1.0, 2.2, 2.8, 4.1, 4.9]
        tr = sci.linear_trend(xs, ys)
        mx, my = 2, sum(ys) / 5
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / 10
        se = math.sqrt(sum((y - (my - b * mx) - b * x) ** 2 for x, y in zip(xs, ys)) / 3 / 10)
        self.assertAlmostEqual(tr["slope"], b)
        self.assertAlmostEqual(tr["hi"] - tr["slope"], 3.182 * se)
        self.assertIsNone(sci.linear_trend([1, 2], [1, 2]))
        self.assertIsNone(sci.linear_trend([3, 3, 3], [1, 2, 3]))

    def test_hall_energy_densities(self):
        self.assertAlmostEqual(sci.FAT_KCAL_PER_KG, 9440.7, places=0)
        self.assertAlmostEqual(sci.LEAN_KCAL_PER_KG, 1816.4, places=0)
        self.assertAlmostEqual(sci.min_weight_change(500 * 28), 1.48, places=2)   # 500 kcal a day for 4 weeks
        lo, hi = sci.weight_rate_bounds(-500, -500)
        self.assertAlmostEqual(lo, -500 / sci.LEAN_KCAL_PER_KG)
        self.assertAlmostEqual(hi, -500 / sci.FAT_KCAL_PER_KG)
        lo, hi = sci.weight_rate_bounds(200, 400)
        self.assertAlmostEqual(lo, 200 / sci.FAT_KCAL_PER_KG)
        self.assertAlmostEqual(hi, 400 / sci.LEAN_KCAL_PER_KG)
        lo, hi = sci.weight_rate_bounds(-300, 300)
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)


class StoreBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build_db(Path(cls.tmp.name) / "gb.db")
        cls.state = Path(cls.tmp.name) / "state"
        cls.h = load_app(Path(cls.tmp.name) / "gb.db", HELIO_STATE=str(cls.state))

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def setUp(self):
        import shutil
        shutil.rmtree(self.state, ignore_errors=True)
        self.h._daycache.clear()

    def food(self, name, per100, **kw):
        return self.h.save_food({"food": {"name": name, "per100": per100, **kw}})["id"]

    def diary(self, d=None):
        return self.h.diary_days(d or DAY, d or DAY)[(d or DAY).isoformat()]


class Foods(StoreBase):
    def test_create_and_read(self):
        fid = self.food("Fiocchi d'avena", OATS, portions=[["1 cup", 40]])
        foods, meals = self.h.foods_load()
        self.assertEqual(foods[fid]["per100"], {**{k: float(v) for k, v in OATS.items()}})
        self.assertEqual(foods[fid]["portions"], [["1 cup", 40.0]])
        pay = self.h.foods_payload()
        item = pay["foods"][0]
        self.assertEqual((T(item["name"]), item["kind"], item["missing"]), ("Fiocchi d'avena", "food", []))
        self.assertTrue(item["check"]["ok"])
        self.assertEqual(pay["factors"], sci.ATWATER)
        self.assertEqual({s["key"] for s in pay["sources"]}, {"eu_kcal", "eu_tol"})
        json.dumps(pay)

    def test_kcal_computed_when_missing(self):
        fid = self.food("No kcal", {"protein": 10, "carbs": 20, "fat": 5, "fiber": 2})
        self.assertEqual(self.h.foods_load()[0][fid]["per100"]["kcal"], 10 * 4 + 20 * 4 + 5 * 9 + 2 * 2)

    def test_validation(self):
        good = {"kcal": 100, "protein": 5, "carbs": 10, "fat": 3, "fiber": 1}
        bad = [
            {**good, "protein": -1}, {**good, "carbs": 101}, {**good, "kcal": 901}, {**good, "fat": "3"},
            {**good, "fiber": True}, {**good, "protein": float("nan")}, {k: v for k, v in good.items() if k != "fat"},
            {"kcal": 100, "protein": 40, "carbs": 40, "fat": 30, "fiber": 0},          # above 100 g per 100 g
            {**good, "sat": 4},                                                         # saturated fat above fat
            {**good, "polyols": 11},                                                    # polyols above carbohydrates
            {**good, "salt": 101}, "tanto",
        ]
        for per100 in bad:
            with self.assertRaises(ValueError, msg=str(per100)[:80]):
                self.h.save_food({"food": {"name": "A", "per100": per100}})
        for name in ("", "   ", "x" * 61, 5, None):
            with self.assertRaises(ValueError, msg=str(name)):
                self.h.save_food({"food": {"name": name, "per100": good}})
        for portions in ([["", 40]], [["cup", 0]], [["cup"]], [["p", 10]] * 9, "cup"):
            with self.assertRaises((ValueError, TypeError), msg=str(portions)[:40]):
                self.h.save_food({"food": {"name": "A", "per100": good, "portions": portions}})
        for body in ({"food": "x"}, {"food": {"name": "A", "per100": good, "kind": "bevanda"}},
                     {"food": {"name": "A", "per100": good, "id": "f_inesistente"}}, {"delete": "f_inesistente"}):
            with self.assertRaises(ValueError, msg=str(body)[:60]):
                self.h.save_food(body)
        self.assertFalse((self.state / "foods.json").exists())

    def test_names_are_unique(self):
        fid = self.food("Riso", {"kcal": 350, "protein": 7, "carbs": 78, "fat": 0.6, "fiber": 1.4})
        with self.assertRaises(ValueError):
            self.food("  riso ", {"kcal": 350, "protein": 7, "carbs": 78, "fat": 0.6, "fiber": 1.4})
        # the same entry can be renamed and saved again
        self.h.save_food({"food": {"id": fid, "name": "Riso", "per100": {"kcal": 352, "protein": 7, "carbs": 78, "fat": 0.6,
                                                                          "fiber": 1.4}}})
        self.assertEqual(self.h.foods_load()[0][fid]["per100"]["kcal"], 352)

    def test_recipe_per_100g_of_cooked_dish(self):
        pasta, tom, oil = self.food("Pasta", PASTA), self.food("Passata", TOMATO), self.food("Oil", OIL)
        rid = self.h.save_food({"food": {"kind": "recipe", "name": "Pasta al pomodoro",
                                         "items": [[pasta, 100], [tom, 150], [oil, 10]], "cooked_g": 400}})["id"]
        foods = self.h.foods_load()[0]
        v, lo, hi = self.h.food_values(foods[rid], foods)
        tot = lambda k, d1, d2, d3: (d1[k] * 100 + d2[k] * 150 + d3[k] * 10) / 100
        self.assertAlmostEqual(v["kcal"], tot("kcal", PASTA, TOMATO, OIL) / 4)        # 400 g dish: /4 per 100 g
        self.assertAlmostEqual(v["protein"], tot("protein", PASTA, TOMATO, OIL) / 4)
        self.assertNotIn("sat", v)                                                    # the passata has no saturated fat
        self.assertNotIn("salt", v)
        (pl, ph), (tl, th), (ol, oh) = sci.food_range(PASTA), sci.food_range(TOMATO), sci.food_range(OIL)
        self.assertAlmostEqual(lo["fat"], (pl["fat"] * 100 + tl["fat"] * 150 + ol["fat"] * 10) / 100 / 4)
        self.assertAlmostEqual(hi["kcal"], (ph["kcal"] * 100 + th["kcal"] * 150 + oh["kcal"] * 10) / 100 / 4)
        # without a cooked weight the raw weight applies
        r2 = self.h.save_food({"food": {"kind": "recipe", "name": "Raw", "items": [[pasta, 100], [oil, 10]]}})["id"]
        self.assertEqual(self.h.foods_load()[0][r2]["cooked_g"], 110)
        # a recipe does not contain recipes; missing ingredients or wrong grams
        for items in ([[rid, 100]], [["f_nessuno", 10]], [[pasta, 0]], [], [[pasta, 10]] * 41):
            with self.assertRaises(ValueError, msg=str(items)[:50]):
                self.h.save_food({"food": {"kind": "recipe", "name": "R", "items": items}})
        with self.assertRaises(ValueError):
            self.h.save_food({"food": {"kind": "recipe", "name": "R", "items": [[pasta, 10]], "cooked_g": 0}})
        with self.assertRaises(ValueError):                                   # a food does not become a recipe
            self.h.save_food({"food": {"kind": "recipe", "id": pasta, "name": "Pasta", "items": [[oil, 10]]}})

    def test_ingredient_change_updates_the_recipe(self):
        pasta, oil = self.food("Pasta", PASTA), self.food("Oil", OIL)
        rid = self.h.save_food({"food": {"kind": "recipe", "name": "R", "items": [[pasta, 100], [oil, 10]]}})["id"]
        before = self.h.food_values(self.h.foods_load()[0][rid], self.h.foods_load()[0])[0]["kcal"]
        self.h.save_food({"food": {"id": pasta, "name": "Pasta", "per100": {**PASTA, "kcal": 400}}})
        foods = self.h.foods_load()[0]
        self.assertAlmostEqual(self.h.food_values(foods[rid], foods)[0]["kcal"] - before, 41 * 100 / 100 / 110 * 100)

    def test_delete_only_when_unused(self):
        pasta, oil = self.food("Pasta", PASTA), self.food("Oil", OIL)
        rid = self.h.save_food({"food": {"kind": "recipe", "name": "Pasta with oil", "items": [[pasta, 100], [oil, 10]]}})["id"]
        mid = self.h.save_meal({"meal": {"name": "Usual lunch", "items": [[rid, 300]]}})["id"]
        with self.assertRaisesRegex(ValueError, "Pasta with oil"):
            self.h.save_food({"delete": pasta})
        with self.assertRaisesRegex(ValueError, "Usual lunch"):
            self.h.save_food({"delete": rid})
        self.assertEqual(self.h.save_meal({"delete": mid}), {"ok": True})
        self.assertEqual(self.h.save_food({"delete": rid}), {"ok": True})
        self.assertEqual(self.h.save_food({"delete": pasta}), {"ok": True})
        self.assertEqual(set(self.h.foods_load()[0]), {oil})

    def test_meal_templates(self):
        oats, pasta = self.food("Oats", OATS), self.food("Pasta", PASTA)
        mid = self.h.save_meal({"meal": {"name": "Usual breakfast", "meal": "breakfast", "items": [[oats, 60], [pasta, 10]]}})["id"]
        pay = self.h.foods_payload()["meals"][0]
        self.assertEqual((T(pay["name"]), pay["meal"], len(pay["items"])), ("Usual breakfast", "breakfast", 2))
        self.assertEqual(pay["kcal"], round(372 * 0.6 + 359 * 0.1))
        for body in ({"meal": {"name": "usual BREAKFAST", "items": [[oats, 10]]}}, {"meal": {"name": "B", "items": []}},
                     {"meal": {"name": "B", "items": [[oats, 10]], "meal": "merenda"}}, {"meal": "x"}, {"delete": "m_nessuno"},
                     {"meal": {"id": "m_nessuno", "name": "B", "items": [[oats, 10]]}}):
            with self.assertRaises(ValueError, msg=str(body)[:60]):
                self.h.save_meal(body)
        self.assertEqual(self.h.diary_op({"date": DAY.isoformat(), "op": "add_meal", "meal": "breakfast", "template": mid}),
                         {"ok": True})
        self.assertEqual([(T(e["name"]), e["g"], e["meal"]) for e in self.diary()["entries"]],
                         [("Oats", 60, "breakfast"), ("Pasta", 10, "breakfast")])

    def test_corrupt_files_are_ignored(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "foods.json").write_text("{not json")
        self.assertEqual(self.h.foods_load(), ({}, {}))
        (self.state / "foods.json").write_text(json.dumps({"foods": {
            "f1": {"kind": "food", "name": "Good", "per100": {"kcal": 50, "protein": 1, "carbs": 10, "fat": 0, "fiber": 1}},
            "f2": {"kind": "food", "name": "Rotto", "per100": {"kcal": "tanto"}},
            "r1": {"kind": "recipe", "name": "With a missing ingredient", "items": [["f9", 10]], "cooked_g": 10},
            "f3": "stringa"}, "meals": {"m1": {"name": "Meal", "items": [["f1", 50], ["f9", 10]]}, "m2": 5}}))
        foods, meals = self.h.foods_load()
        self.assertEqual(set(foods), {"f1"})
        self.assertEqual(meals["m1"]["items"], [["f1", 50.0]])
        (self.state / f"diary-{DAY:%Y-%m}.json").write_text(json.dumps({DAY.isoformat(): {"entries": [
            {"id": "e1"}, "x", {"id": "e2", "meal": "dinner", "name": "Ok", "g": 100, "v": {"kcal": 50, "protein": 1, "carbs": 10,
                                                                                         "fat": 0, "fiber": 1},
                               "lo": {}, "hi": {}}], "complete": "sì"}, "non-una-data": {}}))
        rec = self.diary()
        self.assertEqual([e["id"] for e in rec["entries"]], ["e2"])
        self.assertFalse(rec["complete"])                                      # only True counts as a complete day
        json.dumps(self.h.nutrition_day(DAY))


class Diary(StoreBase):
    def test_add_update_remove_complete(self):
        oats = self.food("Oats", OATS)
        d = DAY.isoformat()
        self.h.diary_op({"date": d, "op": "add", "meal": "breakfast", "food": oats, "g": 60})
        e = self.diary()["entries"][0]
        self.assertEqual((T(e["name"]), e["g"], e["meal"], e["food"], e["kind"]), ("Oats", 60, "breakfast", oats, "food"))
        self.assertEqual(e["v"]["kcal"], 372)
        self.h.diary_op({"date": d, "op": "update", "id": e["id"], "g": 80, "meal": "snacks"})
        e = self.diary()["entries"][0]
        self.assertEqual((e["g"], e["meal"]), (80, "snacks"))
        self.h.diary_op({"date": d, "op": "complete", "complete": True})
        self.assertTrue(self.diary()["complete"])
        self.h.diary_op({"date": d, "op": "remove", "id": e["id"]})
        self.assertEqual(self.diary()["entries"], [])
        self.assertTrue((self.state / f"diary-{DAY:%Y-%m}.json").exists())    # complete day even if empty
        self.h.diary_op({"date": d, "op": "complete", "complete": False})
        self.assertFalse((self.state / f"diary-{DAY:%Y-%m}.json").exists())   # nothing left to remember: file removed
        self.assertFalse(any(self.state.glob("*.tmp")))

    def test_validation(self):
        oats = self.food("Oats", OATS)
        d = DAY.isoformat()
        bad = [
            {"date": d, "op": "add", "meal": "merenda", "food": oats, "g": 50},
            {"date": d, "op": "add", "meal": "lunch", "food": "f_nessuno", "g": 50},
            {"date": d, "op": "add", "meal": "lunch", "food": oats, "g": 0},
            {"date": d, "op": "add", "meal": "lunch", "food": oats, "g": 5001},
            {"date": d, "op": "add", "meal": "lunch", "food": oats, "g": "50"},
            {"date": d, "op": "add_meal", "meal": "lunch", "template": "m_nessuno"},
            {"date": d, "op": "update", "id": "e_nessuno", "g": 50},
            {"date": d, "op": "remove", "id": "e_nessuno"},
            {"date": d, "op": "complete", "complete": "sì"},
            {"date": d, "op": "delete-all"},
            {"date": (DAY + timedelta(days=1)).isoformat(), "op": "add", "meal": "lunch", "food": oats, "g": 50},
            {"date": "1999-12-31", "op": "add", "meal": "lunch", "food": oats, "g": 50},
            {"date": "10/03/2026", "op": "add", "meal": "lunch", "food": oats, "g": 50},
        ]
        for body in bad:
            with self.assertRaises(ValueError, msg=str(body)[:80]):
                self.h.diary_op(body)
        for _ in range(self.h.DAY_ENTRIES_MAX):
            self.h.diary_op({"date": d, "op": "add", "meal": "lunch", "food": oats, "g": 1})
        with self.assertRaisesRegex(ValueError, "at most"):
            self.h.diary_op({"date": d, "op": "add", "meal": "lunch", "food": oats, "g": 1})

    def test_month_files(self):
        oats = self.food("Oats", OATS)
        for x in (date(2026, 2, 28), date(2026, 3, 1)):
            self.h.diary_op({"date": x.isoformat(), "op": "add", "meal": "dinner", "food": oats, "g": 50})
        self.assertTrue((self.state / "diary-2026-02.json").exists())
        self.assertTrue((self.state / "diary-2026-03.json").exists())
        days = self.h.diary_days(date(2026, 2, 27), date(2026, 3, 2))
        self.assertEqual([len(v["entries"]) for v in days.values()], [0, 1, 1, 0])     # 27 and 28 February, 1 and 2 March

    def test_entries_keep_their_values_unless_asked(self):
        oats, oil = self.food("Oats", OATS), self.food("Oil", OIL)
        rid = self.h.save_food({"food": {"kind": "recipe", "name": "Porridge", "items": [[oats, 50], [oil, 5]]}})["id"]
        d = DAY.isoformat()
        self.h.diary_op({"date": d, "op": "add", "meal": "breakfast", "food": oats, "g": 60})
        self.h.diary_op({"date": d, "op": "add", "meal": "breakfast", "food": rid, "g": 100})
        self.h.diary_op({"date": "2026-02-10", "op": "add", "meal": "dinner", "food": oats, "g": 10})
        res = self.h.save_food({"food": {"id": oats, "name": "Oats", "per100": {**OATS, "kcal": 380}}})
        self.assertEqual(res["updated"], 0)
        self.assertEqual(self.diary()["entries"][0]["v"]["kcal"], 372)               # the past does not change by itself
        res = self.h.save_food({"food": {"id": oats, "name": "Avena integrale", "per100": {**OATS, "kcal": 380}},
                                "apply_past": True})
        self.assertEqual(res["updated"], 3)                                         # oats (2 days) and the porridge containing them
        e1, e2 = self.diary()["entries"]
        self.assertEqual((T(e1["name"]), e1["v"]["kcal"], e1["g"]), ("Avena integrale", 380, 60))
        foods = self.h.foods_load()[0]
        self.assertAlmostEqual(e2["v"]["kcal"], round(self.h.food_values(foods[rid], foods)[0]["kcal"], 3))
        self.assertEqual(self.h.diary_days(date(2026, 2, 10), date(2026, 2, 10))["2026-02-10"]["entries"][0]["v"]["kcal"], 380)

    def test_concurrent_writes_are_not_lost(self):
        oats = self.food("Oats", OATS)
        errors = []

        def add():
            try:
                self.h.diary_op({"date": DAY.isoformat(), "op": "add", "meal": "lunch", "food": oats, "g": 10})
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=add) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.diary()["entries"]), 20)
        self.assertEqual(len({e["id"] for e in self.diary()["entries"]}), 20)

    def test_goal(self):
        self.assertEqual(self.h.nutrition_settings(), {"goal": "lose_fat"})     # chosen goal: lose fat
        self.assertEqual(self.h.save_goal({"goal": "maintain"}), {"ok": True})
        self.assertEqual(self.h.nutrition_settings(), {"goal": "maintain"})
        for g in ("ingrassare", None, 1):
            with self.assertRaises(ValueError):
                self.h.save_goal({"goal": g})

    def test_export(self):
        oats = self.food("=HYPERLINK(\"x\")", OATS)
        tom = self.food("Passata", TOMATO)
        d = DAY.isoformat()
        self.h.diary_op({"date": d, "op": "add", "meal": "dinner", "food": oats, "g": 50})
        self.h.diary_op({"date": d, "op": "add", "meal": "breakfast", "food": tom, "g": 200})
        self.h.diary_op({"date": d, "op": "complete", "complete": True})
        text = self.h.export_csv("food", DAY - timedelta(days=3), DAY)
        lines = text.lstrip("﻿").strip().splitlines()
        self.assertEqual(lines[0], "date;meal;food;kind;grams;kcal;protein_g;carbs_g;fat_g;fibre_g;saturated_g;"
                                   "salt_g;day_complete")
        self.assertEqual(lines[1], f"{d};Breakfast;Passata;food;200;68;2,6;11;0,4;3;;;yes")   # in meal order
        self.assertIn("'=HYPERLINK", lines[2])                                   # no formulas run by the spreadsheet
        self.assertNotIn(";=HYPERLINK", lines[2])
        self.assertEqual(len(lines), 3)


class Checks(unittest.TestCase):
    """Thresholds and states of the checks (pure functions, reference weight 74 kg)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build_db(Path(cls.tmp.name) / "gb.db")
        cls.h = load_app(Path(cls.tmp.name) / "gb.db", HELIO_STATE=str(Path(cls.tmp.name) / "state"))

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def checks(self, entries, goal="lose_fat", w=74):
        return {c["key"]: c for c in self.h.nutrient_checks(self.h.intake(entries), w, goal)}

    def food(self, protein=0, carbs=0, fat=0, fiber=0, **kw):
        v = {"protein": protein, "carbs": carbs, "fat": fat, "fiber": fiber, **kw}
        v["kcal"] = sci.atwater_kcal(v)
        return v

    def test_protein_thresholds(self):
        for grams, goal, label, status in ((60, "lose_fat", "Below the EFSA minimum", "certain"),       # below 0.83 g/kg (61 g)
                                           (120, "lose_fat", "Below the reference", "certain"),      # below 1.9 g/kg (141 g)
                                           (160, "lose_fat", "Within the reference", "ok"),
                                           (110, "maintain", "Below the reference", "certain"),    # below 1.6 g/kg (118 g)
                                           (135, "maintain", "Within the reference", "ok")):
            c = self.checks([exact(self.food(protein=grams, carbs=200, fat=60, fiber=30), 100)], goal)["protein"]
            self.assertEqual((T(c["label"]), c["status"]), (label, status), (grams, goal))
            self.assertEqual(c["goal"], round((1.9 if goal == "lose_fat" else 1.6) * 74, 1))
        keys = {s["key"] for s in c["sources"]}
        self.assertEqual(keys, {"efsa_protein", "morton_prot"})
        c = self.checks([exact(self.food(protein=100), 100)])["protein"]
        self.assertEqual({s["key"] for s in c["sources"]}, {"efsa_protein", "refalo"})
        self.assertIn("1.4 g/kg", T(c["display"]))  # 100 g per 74 kg
        # with label tolerances: 135 g declared can be up to 162 g
        c = self.checks([entry(self.food(protein=30, carbs=40, fat=12, fiber=6), 450)])["protein"]
        self.assertEqual(c["status"], "probable")

    def test_fiber(self):
        self.assertEqual(self.checks([exact(self.food(protein=150, carbs=250, fat=60, fiber=30), 100)])["fiber"]["status"], "ok")
        c = self.checks([exact(self.food(protein=150, carbs=250, fat=60, fiber=20), 100)])["fiber"]
        self.assertEqual((c["status"], T(c["label"])), ("certain", "Below 25 g"))
        self.assertEqual({s["key"] for s in c["sources"]}, {"who_cho", "efsa_cho"})

    def test_fat_bands(self):
        def share(fat_kcal_pct):
            # 1000 kcal in total: fat at the wanted share, the rest as carbohydrates
            fat = 1000 * fat_kcal_pct / 100 / 9
            carbs = (1000 - fat * 9) / 4
            return self.checks([exact(self.food(protein=0, carbs=carbs, fat=fat, fiber=0), 100)])["fat"]
        self.assertEqual((share(15)["status"], T(share(15)["label"])), ("certain", "Below the EFSA range"))
        self.assertEqual((share(25)["status"], T(share(25)["label"])), ("ok", "Within the range"))
        self.assertEqual((share(32)["status"], T(share(32)["label"])), ("caution", "Above the WHO suggestion"))
        self.assertEqual((share(40)["status"], T(share(40)["label"])), ("certain", "Above the EFSA range"))
        self.assertEqual({s["key"] for s in share(40)["sources"]}, {"efsa_fat", "who_fat"})

    def test_carbs_range(self):
        def share(pct):
            carbs = 1000 * pct / 100 / 4
            return self.checks([exact(self.food(protein=(1000 - carbs * 4) / 4, carbs=carbs), 100)])["carbs"]
        self.assertEqual(T(share(40)["label"]), "Below the EFSA range")
        self.assertEqual(share(50)["status"], "ok")
        self.assertEqual(T(share(65)["label"]), "Above the EFSA range")

    def test_saturated_fat(self):
        fatty = self.food(protein=50, carbs=150, fat=70, fiber=20, sat=40)       # 360 kcal of saturated fat out of 1,470: 24%
        c = self.checks([exact(fatty, 100)])["sat"]
        self.assertEqual((c["status"], T(c["label"])), ("certain", "Above 10% of calories"))
        self.assertEqual((c["goal"], c["dir"]), (10, "max"))
        ok = self.checks([exact({**fatty, "sat": 10}, 100)])["sat"]
        self.assertEqual(ok["status"], "ok")
        # a food without the value: below the threshold nothing can be said, above it yes (the value is a minimum)
        no_sat = self.food(protein=50, carbs=100, fat=10, fiber=10)
        c = self.checks([exact({**fatty, "sat": 10}, 100), exact(no_sat, 100, "Bread")])["sat"]
        self.assertEqual((c["status"], T(c["label"])), ("info", "Partial data"))  # not "incomplete": only a minimum
        self.assertIn("Bread", T(c["text"]))
        self.assertIn("at least", T(c["display"]))
        self.assertIn("(1 food without the value)", T(c["display"]))
        c = self.checks([exact(fatty, 100), exact(no_sat, 100, "Bread")])["sat"]    # 360 out of 2,180 kcal: already above
        self.assertEqual(c["status"], "certain")
        c = self.checks([exact(no_sat, 100)])["sat"]
        self.assertEqual((c["status"], T(c["label"])), ("incomplete", "No data"))

    def test_salt_is_always_a_minimum(self):
        base = self.food(protein=100, carbs=250, fat=60, fiber=30)
        c = self.checks([exact({**base, "salt": 3}, 100)])["salt"]
        self.assertEqual((c["status"], T(c["label"])), ("info", "Logged salt only"))   # never "ok": cooking salt is missing
        c = self.checks([exact({**base, "salt": 7}, 100)])["salt"]
        self.assertEqual((c["status"], T(c["label"])), ("certain", "Above 5 g"))
        c = self.checks([entry({**base, "salt": 5.5, "kcal": sci.atwater_kcal(base)}, 100)])["salt"]
        self.assertEqual(c["status"], "probable")                                            # 5.5 ± 1.1 g
        self.assertEqual({s["key"] for s in c["sources"]}, {"who_salt"})

    def test_energy_check(self):
        e = self.h.energy_check
        c = e(-700, -900, -550, "lose_fat")
        self.assertEqual((c["status"], T(c["label"])), ("certain", "Deficit above 500 kcal"))
        self.assertEqual({s["key"] for s in c["sources"]}, {"murphy_koehler"})
        self.assertEqual(e(-700, -1000, -300, "lose_fat")["status"], "probable")
        self.assertEqual((e(-300, -600, 0, "lose_fat")["status"], T(e(-300, -600, 0, "lose_fat")["label"])), ("ok", "Moderate deficit"))
        self.assertEqual((e(150, -200, 400, "lose_fat")["status"], T(e(150, -200, 400, "lose_fat")["label"])), ("probable", "No deficit"))
        self.assertEqual(e(300, 50, 600, "lose_fat")["status"], "certain")
        self.assertEqual((e(300, 50, 600, "maintain")["status"], T(e(300, 50, 600, "maintain")["label"])), ("info", "Calorie surplus"))
        self.assertEqual(e(-700, -900, -550, "maintain")["status"], "certain")       # the limit also applies when maintaining
        self.assertEqual(T(e(-700, -900, -550, "lose_fat")["display"]), "−700 kcal a day")

    def test_athlete_reference(self):
        a = self.h.athlete_reference
        self.assertEqual((a(0, 74, None)["category"], a(0, 74, None)["range"]), ("light", [3, 5]))
        self.assertEqual((a(60, 74, 300)["category"], a(60, 74, 300)["range"]), ("moderate", [5, 7]))
        self.assertEqual(a(120, 74, 300)["range"], [6, 10])
        self.assertEqual(a(300, 74, 300)["range"], [8, 12])
        r = a(60, 74, 370)
        self.assertEqual((r["grams"], r["g_per_kg"]), ([370, 518], 5.0))
        self.assertEqual([s["key"] for s in r["sources"]], ["acsm_nutr"])
        self.assertTrue(r["sources"][0]["flagged"])

    def test_recovery_pairs(self):
        t0 = int(datetime(2026, 3, 10, 7, tzinfo=TZ).timestamp())
        tr = lambda a, b, typ, name: {"start": t0 + a * 3600, "end": t0 + b * 3600, "name": name, "label": {"type": typ},
                                      "suggestion": {"type": typ}}
        pairs = self.h.recovery_pairs([tr(0, 0.75, "run", "Run"), tr(6, 7, "strength", "Strength"), tr(9, 10, "work", "Work"),
                                       tr(16, 17, "swim", "Swimming")])
        # standing work does not use glycogen; between strength and swimming there are 9 hours
        self.assertEqual([(p["first"], p["second"], p["gap_h"]) for p in pairs], [("Run", "Strength", 5.2)])
        self.assertEqual(self.h.recovery_pairs([tr(0, 1, "run", "Run"), tr(9.5, 10, "run", "Run")]), [])   # more than 8 hours


def hist_day(d, total=2500, rng=(2400, 2600), worn=1440, has_data=True, trainings=()):
    return {"date": d.isoformat(), "has_data": has_data, "worn_min": worn, "trainings": list(trainings),
            "kcal": {"total": total, "range": list(rng), "active": total - 1649, "active_range": [total - 1649 - 100, total - 1649 + 100],
                     "partial": d >= DAY},
            "profile": {"bmr": 1649}}


class Analysis(StoreBase):
    """Analysis of the day, of the week and weight check, with a known band expenditure."""
    P = {"weight": 74.0, "weight_src": "log"}
    FOOD = {"kcal": 200, "protein": 20, "carbs": 20, "fat": 4.4, "fiber": 2}

    def log(self, fid, d, g, complete=True):
        self.h.diary_op({"date": d.isoformat(), "op": "add", "meal": "lunch", "food": fid, "g": g})
        if complete:
            self.h.diary_op({"date": d.isoformat(), "op": "complete", "complete": True})

    def analysis(self, d, hist):
        return self.h.nutrition_analysis(None, d, self.P, hist)

    def test_day_view(self):
        fid = self.food("Food", self.FOOD)
        self.log(fid, DAY, 1000, complete=False)
        n = self.analysis(DAY, [hist_day(DAY - timedelta(days=i)) for i in range(27, -1, -1)])
        self.assertEqual(n["totals"]["kcal"], 2000)
        lo, hi = sci.food_range(self.FOOD)
        self.assertEqual(n["range"]["kcal"], [round(round(lo["kcal"], 3) * 10, 1), round(round(hi["kcal"], 3) * 10, 1)])
        self.assertTrue(n["today"])
        self.assertTrue(n["expenditure"]["partial"])                      # today: full basal plus the activity so far
        self.assertEqual(n["expenditure"]["kcal"], 1649 + (2500 - 1649))
        self.assertEqual(n["balance"]["kcal"], 2000 - 2500)
        self.assertEqual([m["key"] for m in n["meals"]], ["breakfast", "lunch", "dinner", "snacks"])
        e = n["meals"][1]["entries"][0]
        self.assertEqual((e["g"], e["kcal"], e["per100"]["protein"]), (1000, 2000, 20))
        # day in progress: no judgement on minimums, only progress
        self.assertTrue(all(c["status"] == "in_progress" for c in n["day_checks"]))
        prot = next(c for c in n["day_checks"] if c["key"] == "protein")
        self.assertEqual((prot["dir"], prot["goal"], prot["value"]), ("min", round(1.9 * 74, 1), 200))
        self.h.diary_op({"date": DAY.isoformat(), "op": "complete", "complete": True})
        n = self.analysis(DAY, [hist_day(DAY)])
        self.assertTrue(n["complete"])
        self.assertNotIn("in_progress", {c["status"] for c in n["day_checks"]})
        json.dumps(n)

    def test_salt_excess_shows_even_while_the_day_is_open(self):
        fid = self.food("Salato", {**self.FOOD, "salt": 3})
        self.log(fid, DAY, 300, complete=False)
        n = self.analysis(DAY, [hist_day(DAY)])
        salt = next(c for c in n["day_checks"] if c["key"] == "salt")
        self.assertEqual(salt["status"], "certain")                         # salt can only go up

    def test_week_needs_three_complete_days_and_uses_only_valid_ones(self):
        fid = self.food("Food", self.FOOD)
        d = DAY - timedelta(days=1)                                      # yesterday: every day of the week is over
        hist = [hist_day(d - timedelta(days=i)) for i in range(27, -1, -1)]
        self.log(fid, d - timedelta(days=1), 1000)
        self.log(fid, d - timedelta(days=2), 1000)
        n = self.analysis(d, hist)
        self.assertEqual((n["week"]["days"], n["week"]["checks"], n["week"]["energy"]), (2, [], None))
        self.log(fid, d, 1000)
        self.log(fid, d - timedelta(days=3), 1000, complete=False)       # not complete: does not count
        n = self.analysis(d, hist)
        self.assertEqual(n["week"]["days"], 3)
        self.assertEqual(n["week"]["energy_days"], 3)
        en = n["week"]["energy"]
        lo, hi = sci.food_range(self.FOOD)
        self.assertEqual(en["value"], -500)
        self.assertEqual(en["range"], [round(round(lo["kcal"], 3) * 10 - 2600, 1), round(round(hi["kcal"], 3) * 10 - 2400, 1)])
        self.assertEqual(n["week"]["checks"][0]["key"], "energy")
        # a day with the band on the wrist for less than 20 hours counts for nutrients but not for the balance
        hist[-1] = hist_day(d, worn=1000)
        n = self.analysis(d, hist)
        self.assertEqual((n["week"]["days"], n["week"]["energy_days"]), (3, 2))
        self.assertIsNone(n["week"]["energy"])
        self.assertEqual([x["expenditure"] for x in n["days"]][-1], None)

    def test_today_is_never_in_the_balance(self):
        fid = self.food("Food", self.FOOD)
        for i in range(3):
            self.log(fid, DAY - timedelta(days=i), 1000)
        n = self.analysis(DAY, [hist_day(DAY - timedelta(days=i)) for i in range(27, -1, -1)])
        self.assertEqual((n["week"]["days"], n["week"]["energy_days"]), (3, 2))   # today is complete but the expenditure is not

    def test_deficit_over_500_is_flagged(self):
        fid = self.food("Little", {"kcal": 150, "protein": 20, "carbs": 15, "fat": 1.3, "fiber": 0.2})
        d = DAY - timedelta(days=1)
        hist = [hist_day(d - timedelta(days=i), total=3600, rng=(3500, 3700)) for i in range(27, -1, -1)]
        for i in range(4):
            self.log(fid, d - timedelta(days=i), 1000)
        n = self.analysis(d, hist)
        en = n["week"]["energy"]
        self.assertEqual((T(en["label"]), en["status"]), ("Deficit above 500 kcal", "certain"))   # even in the best case
        brief = self.h.nutrition_brief(n)
        self.assertEqual(brief["issues"][0]["key"], "energy")
        self.assertEqual(brief["issues"][0]["days"], 4)

    def weights(self, d, kgs):
        for i, kg in enumerate(kgs):
            self.h.save_weight(d - timedelta(days=21 - 7 * i), kg)

    def test_weight_check(self):
        fid = self.food("Food", self.FOOD)
        d = DAY - timedelta(days=1)
        hist = [hist_day(d - timedelta(days=i), total=3500, rng=(3400, 3600)) for i in range(27, -1, -1)]
        for i in range(28):
            self.log(fid, d - timedelta(days=i), 1000)
        wc = self.analysis(d, hist)["weight_check"]
        self.assertEqual(wc["status"], "collecting")
        self.assertIn("4 weigh-ins", T(wc["text"]))
        self.assertEqual([s["key"] for s in wc["sources"]], ["hall"])
        lo, hi = sci.food_range(self.FOOD)
        b_lo, b_hi = round(lo["kcal"], 3) * 10 - 3600, round(hi["kcal"], 3) * 10 - 3400
        r_lo, r_hi = sci.weight_rate_bounds(b_lo, b_hi)
        self.weights(d, [74, 74, 74, 74])                                 # 1,500 kcal deficit but stable weight
        wc = self.analysis(d, hist)["weight_check"]
        self.assertEqual(wc["status"], "probable")
        self.assertIn("falling less", T(wc["text"]))
        self.assertEqual(wc["expected_week"], [round(r_lo * 7, 2), round(r_hi * 7, 2)])
        self.assertEqual((wc["trend_week"], wc["balance"]), (0, -1500))
        self.weights(d, [74, 71.9, 69.8, 67.7])                           # -0.3 kg a day: possible
        wc = self.analysis(d, hist)["weight_check"]
        self.assertEqual(wc["status"], "ok")
        self.assertAlmostEqual(wc["trend_week"], -2.1)
        self.weights(d, [74, 64, 54, 44])                                 # faster than the balance allows
        wc = self.analysis(d, hist)["weight_check"]
        self.assertEqual(wc["status"], "probable")
        self.assertIn("falling faster", T(wc["text"]))

    def test_weight_check_needs_enough_days(self):
        fid = self.food("Food", self.FOOD)
        d = DAY - timedelta(days=1)
        hist = [hist_day(d - timedelta(days=i)) for i in range(27, -1, -1)]
        for i in range(20):                                               # 20 complete days: 21 are needed
            self.log(fid, d - timedelta(days=i), 1000)
        self.weights(d, [74, 73.8, 73.6, 73.4])
        wc = self.analysis(d, hist)["weight_check"]
        self.assertEqual((wc["status"], wc["days"]), ("collecting", 20))

    def test_recovery_between_close_sessions(self):
        t = lambda d, h, m=0: int(datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp())
        d = DAY - timedelta(days=1)
        prev = d - timedelta(days=1)
        run = {"start": t(prev, 22), "end": t(prev, 22, 45), "name": "Run", "duration_min": 45, "label": {"type": "run"},
               "suggestion": {"type": "run"}}
        strength = {"start": t(d, 6), "end": t(d, 7), "name": "Strength training · day 1", "duration_min": 60,
                    "label": {"type": "strength"}, "suggestion": {"type": "strength"}}
        hist = [hist_day(prev, trainings=[run]), hist_day(d, trainings=[strength])]
        n = self.analysis(d, hist)
        rc = n["recovery"]                                                # run in the evening, strength the next morning: 7.25 hours
        self.assertEqual([(p["first"], p["second"], p["gap_h"]) for p in rc["pairs"]], [("Run", "Strength training · day 1", 7.2)])
        self.assertEqual(rc["dose"], round(1.02 * 74))
        self.assertIn("best dose has not been established", T(rc["text"]))
        late = {**run, "start": t(prev, 21), "end": t(prev, 21, 45)}      # 8.25 hours: no box
        self.assertIsNone(self.analysis(d, [hist_day(prev, trainings=[late]), hist_day(d, trainings=[strength])])["recovery"])
        self.assertEqual([s["key"] for s in rc["sources"]], ["craven"])
        self.assertEqual(n["athlete"]["minutes"], 60)
        self.assertEqual(n["athlete"]["category"], "moderate")

    def test_summary_insight(self):
        fid = self.food("Proteico", {"kcal": 150, "protein": 20, "carbs": 15, "fat": 1.3, "fiber": 0.2})
        for i in range(1, 4):
            self.log(fid, DAY - timedelta(days=i), 1000)
        s = self.h.summary(DAY)
        self.assertEqual(s["nutrition"]["days"], 3)
        self.assertEqual(s["nutrition"]["issues"][0]["key"], "fiber")    # protein fine, fibre at 2 g
        ins = [i for i in s["insights"] if i["tag"] == "nutrition"]
        self.assertEqual(len(ins), 1)
        self.assertEqual(T(ins[0]["title"]), "Fibre · Below 25 g")
        self.assertIn("last 3 complete days", T(ins[0]["text"]))
        json.dumps(s)


class Api(StoreBase):
    def setUp(self):
        super().setUp()
        self.srv = self.h.ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, path, body=None, headers=None, raw=None):
        hd = {"Content-Type": "application/json", "X-Helio": "1", **(headers or {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, headers=hd if data is not None else {},
                                   method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_full_flow(self):
        code, res = self.req("/api/food", {"food": {"name": "Oats", "per100": OATS, "portions": [["1 cup", 40]]}})
        self.assertEqual(code, 200)
        fid = res["id"]
        code, res = self.req("/api/meal", {"meal": {"name": "Usual breakfast", "items": [[fid, 40]]}})
        self.assertEqual(code, 200)
        self.assertEqual(self.req("/api/diary", {"date": DAY.isoformat(), "op": "add_meal", "meal": "breakfast",
                                                 "template": res["id"]}), (200, {"ok": True}))
        self.assertEqual(self.req("/api/goal", {"goal": "maintain"}), (200, {"ok": True}))
        code, n = self.req(f"/api/nutrition?date={DAY.isoformat()}")
        self.assertEqual(code, 200)
        self.assertEqual((n["totals"]["kcal"], n["goal"]), (round(372 * 0.4, 1), "maintain"))
        code, f = self.req("/api/foods")
        self.assertEqual((code, len(f["foods"]), len(f["meals"])), (200, 1, 1))

    def test_errors_are_json_messages(self):
        code, res = self.req("/api/food", {"food": {"name": "A", "per100": {"protein": 500}}})
        self.assertEqual(code, 400)
        self.assertIn("invalid value", T(res["error"]))
        self.assertEqual(self.req("/api/diary", {"date": "domani", "op": "add"})[0], 400)
        self.assertEqual(self.req("/api/goal", ["lose_fat"])[0], 400)
        self.assertEqual(self.req("/api/food", {"food": {}}, headers={"X-Helio": ""})[0], 403)   # no header
        self.assertEqual(self.req("/api/diary", raw=b"{" + b" " * 5000 + b"}")[0], 400)          # above 4 KB
        self.assertEqual(self.req("/api/food", raw=b"non json")[0], 400)
        self.assertEqual(self.req("/api/nutrition?date=2026-13-40")[0], 200)                     # invalid date: today

    def test_post_limits(self):
        self.assertEqual({k: v for k, v in self.h.Handler.POST_LIMITS.items() if k in ("/api/food", "/api/meal", "/api/diary",
                                                                                         "/api/goal")},
                         {"/api/food": 8192, "/api/meal": 8192, "/api/diary": 4096, "/api/goal": 512})


if __name__ == "__main__":
    unittest.main(verbosity=2)
