"""Active work and the day's energy.

- A work activity costs a standing base chosen for it (or the profile's default), Compendium 2024 (07040 standing
  still 1.3 MET, 07041 standing and moving 1.5, 11620 intermittent lifting 3.8), plus the steps recorded in it at the
  cost of walking above standing (Looney 2019); a minute with the heart rate above the threshold uses the heart rate
  method if that gives more. Standing costs little more than sitting (Saeidifard 2018).
- Every minute belongs to one activity at most: steps and calories count once, also with walks inside a shift and
  heart rate blocks overlapping it.
- The day's energy is one number for Today, Fitness, Food and the CSV (day_energy): today "at least".
- Past days follow the new estimate; the activities entered by hand stay as they were.
"""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from test_helio import SCHEMA, TZ, T, load_app

import helio_science as sci  # noqa: E402 (test_helio puts the app on the path)

DAY = date(2026, 10, 20)  # a Tuesday
ONE = timedelta(days=1)


def at(d, h, m=0):
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp())


def shift_minute(i):
    """Steps of minute i of the shift: each hour 7 minutes walking (100 steps), 24 shuffling (12), 29 almost still (2)."""
    k = i % 60
    return 100 if k < 7 else 12 if k < 31 else 2


def build(path, days=(DAY - ONE, DAY, DAY + ONE), run=False):
    """A band worn all day: asleep 23:00-07:00, 70 bpm and 2 steps a minute while awake; on DAY a shift 08:00-16:00 at
    85 bpm with the steps of shift_minute; run: 15:40-16:30 at 160 bpm and 160 steps (overlapping the shift's end)."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO DEVICE VALUES (1, 'Amazfit Helio Strap')")
    con.execute("INSERT INTO USER VALUES (1, 'test', ?, 1)", (int(datetime(1992, 5, 20, tzinfo=TZ).timestamp() * 1000),))
    con.execute("INSERT INTO USER_ATTRIBUTES VALUES (1, 171, 74, NULL, 8000, 0, NULL, 480, 1)")
    rows = []
    for d in days:
        for t in range(at(d, 0), at(d + ONE, 0), 60):
            local = datetime.fromtimestamp(t, TZ)
            asleep = local.hour >= 23 or local.hour < 7
            steps, hr, kind = (0, 50, 120) if asleep else (2, 70, 80)
            if d == DAY and at(DAY, 8) <= t < at(DAY, 16):
                steps, hr = shift_minute((t - at(DAY, 8)) // 60), 85
            if run and d == DAY and at(DAY, 15, 40) <= t < at(DAY, 16, 30):
                steps, hr = 160, 160
            rows.append((t, 1, 1, 5, steps, kind, hr, 0, 0, 0, 0))
        con.execute("INSERT INTO HUAMI_HEART_RATE_RESTING_SAMPLE VALUES (?,1,1,0,55)", (at(d, 8) * 1000,))
    con.executemany("INSERT INTO HUAMI_EXTENDED_ACTIVITY_SAMPLE VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


class Base(unittest.TestCase):
    NOW = (DAY + ONE, 12)
    RUN = False

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        build(d / "gb.db", run=cls.RUN)
        cls.state = d / "state"
        cls.h = load_app(d / "gb.db", HELIO_STATE=str(cls.state), HELIO_NOW=str(at(*cls.NOW)))

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def setUp(self):
        shutil.rmtree(self.state, ignore_errors=True)
        self.h.clear_caches()

    def add_shift(self, level=None):
        self.assertEqual(self.h.save_label(at(DAY, 8), at(DAY, 16), "work", None, None, manual=True, level=level),
                         {"ok": True})

    def day(self, d=DAY):
        with closing(self.h.connect()) as con:
            return self.h.compute_day(con, d, self.h.profile(con, d))

    def work(self, d=DAY):
        return next(t for t in self.day(d)["trainings"] if t["source"] == "manual")

    def expected(self, met):
        """Standing base for 480 minutes plus the steps of each minute (all below the heart rate threshold)."""
        with closing(self.h.connect()) as con:
            p = self.h.profile(con, DAY)
        walk = sum(sci.walk_kcal(shift_minute(i), p["stride_m"], p["weight"]) for i in range(480))
        return (met - 1) * p["weight"] * 8 + walk


class WorkShift(Base):
    def test_eight_hours_standing_with_some_walking(self):
        self.add_shift()
        w = self.work()
        # before: 1.8 MET standing and 3.3 MET in every minute with more than 10 steps (248 of 480): about 930 kcal,
        # 2.4 MET on average over the whole shift
        old = ((3.3 - 1) * 248 + (1.8 - 1) * 232) * 74 / 60
        self.assertAlmostEqual(old, 932, delta=1)
        new = self.expected(1.5)
        self.assertAlmostEqual(w["kcal"], round(new), delta=1)
        self.assertLess(w["kcal"], 0.6 * old)
        self.assertTrue(1.7 < w["kcal"] / (74 * 8) + 1 < 2.0)                 # average MET of the shift
        self.assertEqual(w["steps"], sum(shift_minute(i) for i in range(480)))
        txt = T(w["kcal_method"])
        for part in ("Standing, moving about", "1.5 MET", "07041", "Looney 2019", "Saeidifard 2018"):
            self.assertIn(part, txt)

    def test_each_level_with_its_compendium_code(self):
        for level, met, code in (("still", 1.3, "07040"), ("moving", 1.5, "07041"), ("heavy", 3.8, "11620")):
            self.setUp()
            self.add_shift(level)
            w = self.work()
            self.assertAlmostEqual(w["kcal"], round(self.expected(met)), delta=1, msg=level)
            self.assertIn(code, T(w["kcal_method"]))
            self.assertEqual((w["label"]["work_level"], w["label"]["work_level_used"]), (level, level))

    def test_profile_default_for_activities_without_a_choice(self):
        self.add_shift()
        self.assertEqual(self.work()["label"]["work_level_used"], "moving")    # default without a profile
        self.h.save_profile({"profile": {"work_level": "still"}})
        w = self.work()
        self.assertEqual((w["label"]["work_level"], w["label"]["work_level_used"]), (None, "still"))
        self.assertAlmostEqual(w["kcal"], round(self.expected(1.3)), delta=1)
        self.add_shift("heavy")                                                # its own choice wins
        self.assertEqual(self.work()["label"]["work_level_used"], "heavy")

    def test_heart_rate_above_the_threshold(self):
        self.add_shift("still")
        with closing(self.h.connect()) as con:
            p = self.h.profile(con, DAY)
        p = {**p, "rest": 55}
        thr = 55 + 0.4 * (p["hrmax"] - 55)
        rows = [{"t": i, "steps": 10, "hr": 170} for i in range(30)] + [{"t": 100 + i, "steps": 10, "hr": 80} for i in range(30)]
        kcal, by_hr = self.h.work_kcal(rows, p, thr, "still")
        hr_part = 30 * sci.hr_active_kcal(170, 55, p["hrmax"], p["vo2max"], p["bmr"], p["weight"])
        low_part = 30 * (0.3 * p["weight"] / 60 + sci.walk_kcal(10, p["stride_m"], p["weight"]))
        self.assertEqual(by_hr, 30)
        self.assertAlmostEqual(kcal, hr_part + low_part)

    def test_choice_is_validated_and_kept_only_for_work(self):
        with self.assertRaises(ValueError):
            self.h.save_label(at(DAY, 8), at(DAY, 16), "work", None, None, manual=True, level="very heavy")
        with self.assertRaises(ValueError):
            self.h.save_profile({"profile": {"work_level": "lazy"}})
        self.h.save_label(at(DAY, 17), at(DAY, 18), "walk", None, None, manual=True, level="heavy")
        self.assertNotIn("work_level", next(iter(json.loads((self.state / "labels.json").read_text()).values())))

    def test_options_for_the_page(self):
        levels = self.h.summary(DAY)["work_levels"]
        self.assertEqual([(x["key"], x["met"], x["code"]) for x in levels],
                         [("still", 1.3, "07040"), ("moving", 1.5, "07041"), ("heavy", 3.8, "11620")])
        self.assertEqual(self.h.profile_payload()["work_levels"], levels)
        self.assertIn("saeidifard", [s["key"] for s in self.h.summary(DAY)["kcal_sources"]])


class StepsAndCaloriesOnce(Base):
    RUN = True

    def test_every_minute_counts_once(self):
        self.add_shift()
        day = self.day()
        trs = day["trainings"]
        work = next(t for t in trs if t["source"] == "manual")
        runs = [t for t in trs if t["source"] == "battito"]
        # the run overlapping the shift's end: only its minutes after 16:00, the others belong to the shift
        self.assertEqual([(t["start"], t["end"]) for t in runs], [(at(DAY, 16), at(DAY, 16, 29))])
        p = day["profile"]
        thr = p["rest"] + 0.4 * (p["hrmax"] - p["rest"])
        inside = {r["t"] for r in day["rows"] if at(DAY, 8) <= r["t"] <= at(DAY, 16, 29)}
        outside = sum(self.h.kcal_active_minute(r, p, thr) for r in day["rows"] if r["t"] not in inside
                      and r.get("RAW_KIND") not in (self.h.KIND_NOT_WORN, self.h.KIND_CHARGING))
        self.assertAlmostEqual(day["kcal"]["active"], round(outside + work["kcal"] + runs[0]["kcal"]), delta=2)
        # steps: the day's are the band's, once; the walks inside the shift are part of it
        self.assertEqual(day["steps"], sum(r["steps"] for r in day["rows"]))
        self.assertEqual(work["steps"] + runs[0]["steps"],
                         sum(r["steps"] for r in day["rows"] if r["t"] in inside))
        s = self.h.summary(DAY)
        inside_walks = [x for x in s["workouts"] if x["source"] == "gadgetbridge" and at(DAY, 8) <= x["start"] < at(DAY, 16)]
        self.assertTrue(inside_walks)                                          # the 5-minute walks
        self.assertTrue(all(x["start"] in work["segments"] for x in inside_walks))


class OneEnergyEverywhere(Base):
    NOW = (DAY, 20, 30)   # the day is not over

    def test_today_today_fitness_and_food_agree(self):
        self.add_shift()
        s = self.h.summary(DAY)
        n = self.h.nutrition_day(DAY)
        t = self.h.trends(DAY)
        self.assertTrue(s["kcal"]["partial"])
        self.assertEqual(n["expenditure"]["kcal"], s["kcal"]["total"])
        self.assertEqual(n["expenditure"]["range"], s["kcal"]["range"])
        self.assertTrue(n["expenditure"]["partial"])
        self.assertEqual(t["days"][-1]["kcal"], s["kcal"]["total"])
        # the basal metabolism of the whole day: the activity can only add to it
        self.assertEqual(s["kcal"]["bmr"], s["profile"]["bmr"])
        self.assertEqual(s["kcal"]["total"], s["kcal"]["bmr"] + s["kcal"]["active"])

    def test_past_day_agrees_too(self):
        d = DAY - ONE
        s, n = self.h.summary(d), self.h.nutrition_day(d)
        self.assertFalse(s["kcal"]["partial"])
        self.assertEqual((n["expenditure"]["kcal"], n["expenditure"]["partial"]), (s["kcal"]["total"], False))
        csv = self.h.export_csv("days", d, d, "std", "en").splitlines()
        cols = csv[0].split(",")
        row = dict(zip(cols, csv[1].split(",")))
        kcal_col = next(c for c in cols if "kcal" in c.lower() and "active" not in c.lower() and "basal" not in c.lower())
        self.assertEqual(int(float(row[kcal_col])), s["kcal"]["total"])


class PastDays(Base):
    def test_old_activities_get_the_new_estimate_and_stay_as_they_were(self):
        # a shift saved by an older version: no intensity, its own fields
        old = {f"m{at(DAY, 8)}": {"start": at(DAY, 8), "end": at(DAY, 16), "type": "work", "day": None, "load_kg": 0,
                                  "features": {"dur": 480}, "saved": 1792000000, "manual": True}}
        self.state.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(old, ensure_ascii=False, indent=1)
        (self.state / "labels.json").write_text(raw)
        w = self.work()
        self.assertAlmostEqual(w["kcal"], round(self.expected(1.5)), delta=1)
        self.assertEqual(w["source"], "manual")
        self.assertEqual((w["start"], w["end"]), (at(DAY, 8), at(DAY, 16)))
        self.assertEqual((self.state / "labels.json").read_text(), raw)       # untouched, byte for byte


if __name__ == "__main__":
    unittest.main()
