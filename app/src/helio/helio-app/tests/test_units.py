"""Tests of single server functions (calories by type, jumps, day status, classification)
and a check of the newer functions on the real database, if present (HELIO_LIVE_DB)."""
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from test_helio import T, DAY, LIVE_DB, build_db, load_app
from test_trainings import minute_rows

import helio_science as sci

P = {"weight": 74.0, "bmr": 1649, "stride_m": 0.72, "age": 33, "sex": "M", "hrmax": 185, "rest": 55, "vo2max": 45.8}


class Units(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build_db(Path(cls.tmp.name) / "gb.db")
        cls.h = load_app(Path(cls.tmp.name) / "gb.db", HELIO_STATE=str(Path(cls.tmp.name) / "state"))

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    # ---------------------------------------------------------------- calories per minute
    def test_minute_below_moderate_counts_only_walking(self):
        thr = 55 + 0.4 * (185 - 55)
        r = {"hr": thr - 1, "steps": 0}
        self.assertEqual(self.h.kcal_active_minute(r, P, thr), 0.0)
        r = {"hr": thr - 1, "steps": 100}
        self.assertAlmostEqual(self.h.kcal_active_minute(r, P, thr), sci.walk_kcal(100, 0.72, 74))

    def test_minute_above_moderate_uses_heart_rate(self):
        thr = 55 + 0.4 * (185 - 55)
        r = {"hr": 150, "steps": 0}
        self.assertAlmostEqual(self.h.kcal_active_minute(r, P, thr), sci.hr_active_kcal(150, 55, 185, 45.8, 1649, 74))

    def test_minute_takes_the_larger_estimate(self):
        thr = 55 + 0.4 * (185 - 55)
        r = {"hr": int(thr) + 1, "steps": 160}                     # brisk walking with heart rate just above threshold
        expect = max(sci.hr_active_kcal(r["hr"], 55, 185, 45.8, 1649, 74), sci.walk_kcal(160, 0.72, 74))
        self.assertAlmostEqual(self.h.kcal_active_minute(r, P, thr), expect)

    def test_minute_without_heart_rate(self):
        self.assertAlmostEqual(self.h.kcal_active_minute({"hr": None, "steps": 50}, P, 110), sci.walk_kcal(50, 0.72, 74))

    # ---------------------------------------------------------------- calories by type
    def feat(self, **kw):
        f = {"dur": 50, "steps": 0, "spm": 0, "cad": 0, "move": 0, "hrr": 0.4, "hrr_max": 0.6, "hr_sd": 5}
        f.update(kw)
        return f

    def test_typed_methods(self):
        part = minute_rows([(30, 130, 100, 80)])
        cases = {"strength": "Compendium 2024", "swim": "Compendium 2024", "work": "Saeidifard 2018",
                 "run": "heart rate reserve", "rope": "heart rate reserve", "other": "heart rate reserve",
                 "walk": "heart rate reserve", "hike": "heart rate reserve"}
        for t, word in cases.items():
            kcal, method = self.h.typed_kcal(t, part, self.feat(), P, 110, 0, 57)
            method = T(method)
            self.assertIn(word, method, t)
            self.assertGreaterEqual(kcal, 0, t)

    def test_walking_with_backpack(self):
        part = minute_rows([(30, 100, 100, 80)])
        kcal, method = self.h.typed_kcal("walk", part, self.feat(), P, 110, 10, 57)
        method = T(method)
        self.assertIn("Pandolf", method)
        self.assertIn("underestimate", method)
        self.assertEqual(self.h.kcal_err("walk", 10), (sci.PANDOLF_UNDER, True))
        self.assertEqual(self.h.kcal_err("walk", 0), (sci.ACTIVE_ERR, False))
        self.assertEqual(self.h.kcal_err("hike", 10), (sci.ACTIVE_ERR, False))

    def test_trekking_never_below_pandolf(self):
        part = minute_rows([(60, 100, 100, 80)])                    # low heart rate: Pandolf is the floor
        kcal, method = self.h.typed_kcal("hike", part, self.feat(), P, 110, 12, 57)
        method = T(method)
        pand = sum(max(0, self.h.pandolf_kcal_min(100, P, 12, eta=1.2) - 1649 / 1440) for _ in range(60))
        self.assertAlmostEqual(kcal, max(pand, sum(self.h.kcal_active_minute(r, P, 110) for r in part)))
        self.assertIn("Pandolf", method)

    def test_work_shift_standing_base_plus_steps(self):
        part = minute_rows([(60, 80, 0, 80), (60, 85, 40, 80)])
        walk = 60 * sci.walk_kcal(40, 0.72, 74)
        for level, met in (("still", 1.3), ("moving", 1.5), ("heavy", 3.8)):   # Compendium 07040, 07041, 11620
            kcal, method = self.h.typed_kcal("work", part, self.feat(), P, 110, 0, 57, level)
            self.assertAlmostEqual(kcal, (met - 1) * 74 * 2 + walk, msg=level)
            self.assertIn(self.h.WORK_LEVELS[level][1], T(method))

    # ---------------------------------------------------------------- jumps
    def test_jump_minutes(self):
        part = minute_rows([(10, 125, 130, 80), (20, 130, 5, 80), (1, 120, 110, 80), (5, 120, 0, 80)])
        self.assertEqual(len(self.h.jump_minutes("strength", part)), 10)          # the isolated minute is not rope
        self.assertEqual(len(self.h.jump_minutes("rope", part)), 31)          # in a rope session every minute with steps
        self.assertEqual(self.h.jump_minutes("walk", part), set())
        self.assertEqual(self.h.jump_minutes(None, part), set())

    # ---------------------------------------------------------------- type and status of the day
    def test_training_type(self):
        t = {"label": {"type": "strength"}, "suggestion": {"type": "walk"}}
        self.assertEqual(self.h.training_type(t), "strength")
        self.assertEqual(self.h.training_type({"label": None, "suggestion": {"type": "run"}}), "run")
        self.assertIsNone(self.h.training_type({}))

    def test_day_status(self):
        f = self.h.day_status
        today = date(2026, 3, 22)
        tr = lambda t: [{"label": {"type": t}}]
        past = date(2026, 3, 20)
        self.assertEqual(f(date(2026, 3, 23), today, ["strength"], []), "futuro")
        self.assertEqual(f(past, today, [], []), "rest")
        self.assertEqual(f(past, today, [], tr("run")), "extra")
        self.assertEqual(f(past, today, ["work"], []), "missing")
        self.assertEqual(f(today, today, ["work"], []), "today")
        self.assertEqual(f(past, today, ["work"], tr("work")), "done")
        self.assertEqual(f(past, today, ["run", "rope"], tr("rope")), "done")
        self.assertEqual(f(past, today, ["strength"], tr("run")), "different")
        self.assertEqual(f(past, today, ["strength"], []), "skipped")
        self.assertEqual(f(today, today, ["strength"], []), "today")

    def test_walk_with_short_stops_is_a_walk(self):
        # steps in almost every minute but heart rate dropping at the breaks: not strength
        f = self.feat(dur=40, spm=91, cad=100, move=0.93, hrr=0.45, hr_sd=15.7)
        self.assertEqual(self.h.rule_type(f, {"types": [], "text": "", "day": None})[0], "walk")
        rope = self.feat(dur=60, spm=61, cad=120, move=0.5, hrr=0.44, hr_sd=15.0)   # strength with rope: stays strength
        self.assertEqual(self.h.rule_type(rope, {"types": [], "text": "", "day": None})[0], "strength")

    def test_plan_for_strength_day(self):
        plan = {"2": {"text": "F1", "types": ["strength"], "day": 1}, "6": {"text": "F3", "types": ["strength"], "day": 3}}
        orig = self.h.load_plan
        self.h.load_plan = lambda: plan
        try:
            self.assertEqual(T(self.h.plan_for_strength_day(1)["text"]), "F1")
            self.assertEqual(T(self.h.plan_for_strength_day(3)["text"]), "F3")
            self.assertIsNone(self.h.plan_for_strength_day(2))
        finally:
            self.h.load_plan = orig

    def test_csv_text(self):
        it = self.h.csv_text(["date", "kcal"], [[1.5, "x;y"]], "eu")
        self.assertEqual(it, '﻿date;kcal\r\n1,5;"x;y"\r\n')
        std = self.h.csv_text(["date", "kcal"], [[1.5, "x;y"]], "std", "en")
        self.assertEqual(std, "date,kcal\r\n1.5,x;y\r\n")

    def test_parse_iso(self):
        self.assertEqual(self.h.parse_iso("2026-03-10"), date(2026, 3, 10))
        for bad in (None, "", "10/03/2026", "2026-13-01", 20260310):
            with self.assertRaises(ValueError):
                self.h.parse_iso(bad)

    def test_label_load_and_day_validation(self):
        with self.assertRaises(ValueError):
            self.h.save_label(1, 2, "strength", True, 0)                # True is not day 1

    def test_summary_is_complete(self):
        s = self.h.summary(DAY)
        for k in ("advice", "vo2max", "sri", "proprietary", "kcal_sources", "hrmax_sources", "load_note", "sources_index",
                  "steps_band", "jumps", "trimp"):
            self.assertIn(k, s)
        self.assertEqual(s["steps"], s["steps_band"] - s["jumps"])
        json.dumps(s)


@unittest.skipUnless(Path(LIVE_DB).is_file(), "real database not available")
class LiveFeatures(unittest.TestCase):
    """The newer functions on your real data: no errors and values within physiological limits."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.h = load_app(Path(LIVE_DB), HELIO_STATE=str(Path(cls.tmp.name) / "state"))
        os.environ.pop("HELIO_NOW", None)  # real data: today's date

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def test_last_days(self):
        today = self.h.now().date()
        for i in range(3):
            d = today - timedelta(days=i)
            s = self.h.summary(d)
            json.dumps(s)
            k = s["kcal"]
            self.assertTrue(k["range"][0] <= k["total"] <= k["range"][1], d)
            self.assertTrue(0 <= s["jumps"] <= s["steps_band"], d)
            self.assertGreaterEqual(s["profile"]["hrmax"], 150)
            self.assertTrue(s["profile"]["hrmax"] >= s["profile"]["hrmax_pred"] or s["profile"]["hrmax_src"] == "set")
            v = s["vo2max"]["value"]
            self.assertTrue(v is None or 20 <= v <= 90, v)
            r = s["sri"]["value"]
            self.assertTrue(r is None or -100 <= r <= 100, r)
            for t in s["trainings"]:
                self.assertTrue(t["kcal_range"][0] <= t["kcal"] <= t["kcal_range"][1])
                self.assertTrue(0 <= t["jumps"] <= t["steps"])
                self.assertGreaterEqual(t["trimp"], 0)

    def test_week_strength_and_export(self):
        today = self.h.now().date()
        json.dumps(self.h.week_summary(today))
        json.dumps(self.h.week_summary(today, "previous"))
        json.dumps(self.h.strength_day(today))
        text = self.h.export_csv("days", today - timedelta(days=13), today)
        self.assertEqual(len(text.strip().splitlines()), 15)
        self.h.export_csv("workouts", today - timedelta(days=13), today, "std")

    def test_peaks_are_plausible(self):
        from contextlib import closing
        with closing(self.h.connect()) as con:
            peaks = self.h.hr_peaks(con)
        for d, (v, t) in peaks.items():
            self.assertTrue(self.h.PEAK_MIN <= v <= self.h.HR_MAX, (d, v))


if __name__ == "__main__":
    unittest.main(verbosity=2)
