"""Data cleaning tests.

Every cleaning rule is tied to a scientific source (CLEAN_RULES and SOURCES in helio_app).
These tests check three things:
  1. metadata: every rule has sources with links and the thresholds are those given by the sources;
  2. the rule removes the measurement errors it must remove;
  3. the rule does NOT touch real data, including real peaks during a hike.
"""
from contextlib import closing
import json
import math
import os
import sqlite3
import statistics
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from test_helio import T, D0, DAY, LIVE_DB, build_db, hm, load_app


def rows_from(hrs, steps=None, inten=None, t0=D0 + 15 * 3600):
    steps = steps or [0] * len(hrs)
    inten = inten or [5] * len(hrs)
    return [{"t": t0 + 60 * i, "hr": h, "steps": s, "raw_steps": s, "RAW_INTENSITY": n}
            for i, (h, s, n) in enumerate(zip(hrs, steps, inten))]


class AppMixin:
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "gb.db"
        build_db(cls.db)
        cls.prepare(cls.db)
        cls.h = load_app(cls.db)

    @classmethod
    def prepare(cls, db):
        pass

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


# ---------------------------------------------------------------- 1. metadata and sources

class RulesAndSources(AppMixin, unittest.TestCase):
    def test_every_rule_has_scientific_sources(self):
        self.assertGreaterEqual(len(self.h.CLEAN_RULES), 4)
        ids = set()
        for r in self.h.CLEAN_RULES:
            self.assertNotIn(r["id"], ids)
            ids.add(r["id"])
            cat = self.h.catalog("it"); self.assertTrue(f"rule.{r['id']}.title" in cat and len(cat[f"rule.{r['id']}.text"]) > 60, r["id"])
            self.assertTrue(r["sources"], r["id"])
            for k in r["sources"]:
                src = self.h.SOURCES[k]
                self.assertTrue(src["url"].startswith("https://"), k)
                self.assertRegex(src["title"], r"\d{4}|American Academy|Australian Institute", k)

    def test_all_health_and_cleaning_sources_are_https(self):
        for k, src in self.h.SOURCES.items():
            self.assertTrue(src["url"].startswith("https://"), k)

    def test_thresholds_match_the_sources(self):
        h = self.h
        self.assertEqual(h.SPO2_DIP, 3)                      # AASM: desaturation >= 3%
        self.assertEqual(h.BASE_DAYS, 60)                    # AIS: 60-day rolling baseline
        self.assertEqual(h.ROLL_MIN, 3)                      # AIS: at least 3 measurements for the 7-day average
        self.assertEqual(h.MOVE_INTENSITY, round(0.4 * 256))  # Gadgetbridge: active minute = intensity > 0.4
        self.assertGreaterEqual(h.SPIKE_BPM, 20)             # conservative: below 20 bpm a jump can be real
        self.assertLessEqual(h.SPO2_NEIGHBOR_S, 15 * 60)

    def test_summary_exposes_rules_and_counts(self):
        s = self.h.summary(DAY)
        q = s["quality"]
        json.dumps(q)
        self.assertEqual([r["id"] for r in q["rules"]], [r["id"] for r in self.h.CLEAN_RULES])
        for r in q["rules"]:
            self.assertTrue(all(x["url"].startswith("https://") for x in r["sources"]))
        for k in ("hr_fixes", "hr_samples", "spo2_motion", "spo2_dips", "spo2_samples"):
            self.assertIn(k, q)


# ---------------------------------------------------------------- 2-3. heart rate

class HeartRateCleaning(AppMixin, unittest.TestCase):
    def test_isolated_spike_at_rest_is_removed(self):
        r = self.h.clean_hr(rows_from([60, 61, 95, 60, 62]))
        self.assertIsNone(r[2]["hr"])
        self.assertEqual((r[2]["hr_raw"], r[2]["hr_fix"]), (95, "picco"))
        self.assertEqual([x["hr"] for x in r if x is not r[2]], [60, 61, 60, 62])

    def test_dropout_is_removed(self):
        r = self.h.clean_hr(rows_from([70, 72, 40, 71, 70]))
        self.assertEqual((r[2]["hr"], r[2]["hr_fix"]), (None, "caduta"))

    def test_spike_with_steps_is_kept(self):
        r = self.h.clean_hr(rows_from([90, 92, 125, 95, 93], steps=[0, 10, 80, 10, 0]))
        self.assertEqual(r[2]["hr"], 125)

    def test_spike_with_wrist_movement_is_kept(self):
        r = self.h.clean_hr(rows_from([90, 92, 125, 95, 93], inten=[5, 5, 150, 5, 5]))
        self.assertEqual(r[2]["hr"], 125)

    def test_steep_climb_peaks_are_kept(self):
        # synthetic minutes of short, steep climbs on a hike: heart rate rises fast while stepping
        cases = [([120, 115, 136, 161, 142, 125, 117], [5, 10, 80, 42, 0, 0, 0]),
                 ([112, 111, 138, 160, 157, 130, 113], [0, 0, 70, 88, 15, 0, 0]),
                 ([128, 120, 141, 163, 156, 133, 122], [40, 100, 104, 96, 20, 5, 33])]
        for hrs, steps in cases:
            r = self.h.clean_hr(rows_from(hrs, steps))
            self.assertEqual([x["hr"] for x in r], hrs)

    def test_transition_between_levels_is_kept(self):
        # neighbours that disagree with each other: a change of level, not an artefact
        r = self.h.clean_hr(rows_from([60, 60, 95, 85, 85]))
        self.assertEqual(r[2]["hr"], 95)

    def test_gap_in_data_is_not_treated_as_neighbour(self):
        r = rows_from([60, 95, 60])
        r[2]["t"] += 600
        self.assertEqual(self.h.clean_hr(r)[1]["hr"], 95)

    def test_small_jumps_are_kept(self):
        r = self.h.clean_hr(rows_from([60, 60, 80, 60, 60]))
        self.assertEqual(r[2]["hr"], 80)

    def test_gadgetbridge_sessions_still_use_original_values(self):
        hrs = [95] * 20
        hrs[10] = 160
        steps = [100] * 20
        steps[9] = steps[10] = 0
        cleaned = self.h.clean_hr(rows_from(hrs, steps))
        raw = rows_from(hrs, steps)
        for x in raw:
            x["hr_raw"] = x["hr"]
        self.assertEqual(self.h.gb_sessions(cleaned, 0.71), self.h.gb_sessions(raw, 0.71))


class HeartRateInSummary(AppMixin, unittest.TestCase):
    @classmethod
    def prepare(cls, db):
        with closing(sqlite3.connect(db)) as con:
            # artefact at rest at 15:00 (HR 60 around, no steps) and a drop at 16:00
            con.execute("UPDATE HUAMI_EXTENDED_ACTIVITY_SAMPLE SET HEART_RATE=190 WHERE TIMESTAMP=?", (hm(15, 0),))
            con.execute("UPDATE HUAMI_EXTENDED_ACTIVITY_SAMPLE SET HEART_RATE=31 WHERE TIMESTAMP=?", (hm(16, 0),))
            con.commit()

    def test_artifacts_removed_from_day_stats(self):
        s = self.h.summary(DAY)
        hr = s["hr"]
        self.assertEqual((hr["raw_max"], hr["max"]), (190, 165))   # 165 = the real run at 18:00
        self.assertEqual((hr["raw_min"], hr["min"]), (31, 50))
        fixes = {(f["t"], f["v"], f["why"]) for f in s["quality"]["hr_fixes"]}
        self.assertEqual(fixes, {(hm(15, 0), 190, "picco"), (hm(16, 0), 31, "caduta")})
        self.assertNotIn(190, [v for _, v in hr["series"]])

    def test_zones_and_calories_ignore_artifact(self):
        s = self.h.summary(DAY)
        self.assertEqual(s["zones"][4], 0)  # 190 would have ended up in the maximal zone


# ---------------------------------------------------------------- SpO2

class SpO2Cleaning(AppMixin, unittest.TestCase):
    T = D0 + 2 * 3600

    def rows(self, n=40, moving=()):
        r = rows_from([55] * n, t0=self.T)
        for i in moving:
            r[i]["steps"] = 60
        return r

    def test_motion_readings_are_removed(self):
        vals = [(self.T + 300 * i, v) for i, v in enumerate([98, 91, 97])]
        out = self.h.spo2_clean(vals, self.rows(moving=[5]))
        self.assertEqual((out["motion_removed"], out["min_raw"], out["min"]), (1, 91, 98))

    def test_isolated_dip_is_counted_not_hidden(self):
        vals = [(self.T + 300 * i, v) for i, v in enumerate([99, 97, 94, 98])]
        out = self.h.spo2_clean(vals, self.rows())
        self.assertEqual((out["min"], out["min_raw"], out["isolated_dips"]), (97, 94, 1))

    def test_sustained_drop_is_kept(self):
        # a dip lasting several readings is real and must stay in the minimum
        vals = [(self.T + 300 * i, v) for i, v in enumerate([98, 95, 94, 94, 97])]
        out = self.h.spo2_clean(vals, self.rows())
        self.assertEqual((out["min"], out["isolated_dips"]), (94, 0))

    def test_readings_far_apart_are_not_pairs(self):
        vals = [(self.T, 98), (self.T + 3600, 93), (self.T + 7200, 98), (self.T + 7500, 97)]
        out = self.h.spo2_clean(vals, self.rows(n=200))
        self.assertEqual((out["min"], out["isolated_dips"]), (98, 1))

    def test_all_in_motion(self):
        vals = [(self.T + 300 * i, 97) for i in range(3)]
        out = self.h.spo2_clean(vals, self.rows(moving=[0, 5, 10]))
        self.assertIsNone(out["avg"])
        self.assertEqual(out["motion_removed"], 3)

    def test_no_readings(self):
        self.assertIsNone(self.h.spo2_clean([], self.rows()))


# ---------------------------------------------------------------- baseline

class Baselines(AppMixin, unittest.TestCase):
    def test_collecting_until_14_days(self):
        b = self.h.baseline([None] * 50 + [55] * 10)
        self.assertEqual((b["status"], b["mean"], b["n_base"]), ("collecting", None, 10))
        self.assertEqual(b["avg7"], 55)

    def test_rolling_average_needs_three_measures(self):
        b = self.h.baseline([55] * 30 + [None] * 5 + [54, 56])
        self.assertIsNone(b["avg7"])          # only 2 measurements in the last 7 days
        self.assertIsNotNone(b["mean"])       # the normal range already exists (32 days)
        self.assertEqual(b["status"], "collecting")

    def test_resting_hr_formula_and_status(self):
        vals = [54, 56] * 26 + [61] * 7
        b = self.h.baseline(vals, floor=1.0)
        m, sd = statistics.mean(vals), statistics.stdev(vals)
        swc = max(0.5 * sd, 1.0)
        self.assertEqual(b["mean"], round(m, 1))
        self.assertAlmostEqual(b["low"], round(m - swc, 1))
        self.assertAlmostEqual(b["high"], round(m + swc, 1))
        self.assertEqual((b["avg7"], b["status"]), (61, "above"))

    def test_floor_is_measurement_resolution(self):
        b = self.h.baseline([55] * 30, floor=1.0)
        self.assertEqual((b["low"], b["high"], b["status"]), (54, 56, "normal"))

    def test_hrv_uses_log(self):
        vals = [50, 72] * 20 + [40, 42, 38, 41, 39, 40, 43]
        b = self.h.baseline(vals, log=True, floor=0.02)
        ln = [math.log(v) for v in vals]
        self.assertAlmostEqual(b["avg7"], round(math.exp(statistics.mean(ln[-7:])), 1))
        self.assertAlmostEqual(b["mean"], round(math.exp(statistics.mean(ln)), 1))
        # band symmetric on the logarithm, hence geometric in milliseconds
        self.assertAlmostEqual(math.log(b["mean"]) - math.log(b["low"]), math.log(b["high"]) - math.log(b["mean"]), places=2)
        self.assertEqual(b["status"], "below")

    def test_readiness_follows_hrv_trend(self):
        h = self.h
        p = {"sleep_goal_min": 480}
        day = {"resting": 55}
        prev = [{"resting": 55}] * 14
        low = {"rhr": h.baseline([54, 56] * 30, floor=1.0), "hrv": h.baseline([60] * 53 + [40] * 7, log=True, floor=0.02)}
        ok = {"rhr": low["rhr"], "hrv": h.baseline([60] * 60, log=True, floor=0.02)}
        r_low = h.readiness(day, prev, None, None, p, low)
        r_ok = h.readiness(day, prev, None, None, p, ok)
        self.assertLess(r_low["score"], r_ok["score"])
        self.assertTrue(any("HRV" in x and "below" in x for x in T(r_low["parts"])))

    def test_summary_baselines_shape(self):
        s = self.h.summary(DAY)
        for k in ("rhr", "hrv"):
            b = s["baselines"][k]
            for f in ("today", "avg7", "mean", "low", "high", "status", "n_base", "need"):
                self.assertIn(f, b)
        self.assertEqual(s["baselines"]["rhr"]["today"], 52)


# ---------------------------------------------------------------- real data

@unittest.skipUnless(Path(LIVE_DB).is_file(), "real database not available")
class LiveCleaning(unittest.TestCase):
    """On your data: cleaning must remove very little and only what matches the rules."""

    @classmethod
    def setUpClass(cls):
        os.environ.pop("HELIO_NOW", None)
        cls.h = load_app(LIVE_DB)
        os.environ.pop("HELIO_NOW", None)
        import importlib
        cls.h = importlib.reload(cls.h)
        cls.today = cls.h.now().date()

    def test_removed_values_follow_the_rules(self):
        h = self.h
        for i in range(7):
            d = self.today - timedelta(days=i)
            with closing(h.connect()) as con:
                rows = h.activity_rows(con, h.midnight(d), h.midnight(d + timedelta(days=1)))
            fixes = [k for k, r in enumerate(rows) if r.get("hr_fix")]
            valid = sum(1 for r in rows if r["hr_raw"] is not None)
            if valid:
                self.assertLessEqual(len(fixes) / valid, 0.01, d)  # at most 1% of the minutes
            for k in fixes:
                a, r, b = rows[k - 1], rows[k], rows[k + 1]
                self.assertLessEqual(abs(a["hr_raw"] - b["hr_raw"]), h.SPIKE_AGREE)
                if r["hr_fix"] == "picco":
                    self.assertGreaterEqual(r["hr_raw"] - max(a["hr_raw"], b["hr_raw"]), h.SPIKE_BPM)
                    self.assertFalse(h.is_moving(a, r))
                else:
                    self.assertGreaterEqual(min(a["hr_raw"], b["hr_raw"]) - r["hr_raw"], h.SPIKE_BPM)

    def test_spo2_and_baselines_consistent(self):
        for i in range(7):
            d = self.today - timedelta(days=i)
            s = self.h.summary(d)
            sp = s["spo2"]
            if sp:
                self.assertTrue(70 <= sp["min_raw"] <= sp["min"] <= sp["avg"] <= sp["max"] <= 100, (d, sp))
            for k in ("rhr", "hrv"):
                b = s["baselines"][k]
                if b["mean"] is not None:
                    self.assertLess(b["low"], b["mean"])
                    self.assertLess(b["mean"], b["high"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
