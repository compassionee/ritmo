"""Tests of the newer features on three weeks of test data (2-22 March 2026, Monday to Sunday).

Every day: sleep 00:00-07:00, walk 09:00-09:39, resting heart rate 54-56 and overnight HRV.
Tuesday, Thursday and Saturday: strength training with rope at the start and at the end (18:00-19:01).
Wednesday: run (18:00-18:29); on 18 March with a real 198 bpm peak held for 2 minutes.
Friday: rope only (07:30-07:49). On 11 March there are also artefacts that must not count as max HR.
"""
import csv
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from test_helio import T, SCHEMA, TZ, load_app

START, END = date(2026, 3, 2), date(2026, 3, 22)
NOW = int(datetime(2026, 3, 22, 23, 59, tzinfo=TZ).timestamp())
PEAK_AT = int(datetime(2026, 3, 18, 18, 20, tzinfo=TZ).timestamp())
PLAN = {
    "1": {"text": "Full rest", "types": [], "exercises": []},
    "2": {"text": "Strength training – day 1", "types": ["strength"], "day": 1,
          "exercises": [["Wide-grip pull-ups", "4×8"], ["Parallel bar dips", "3×10"], ["Plank", "3×1 min"],
                        ["Rope finisher", "10 min"]]},
    "3": {"text": "Run or skipping rope", "types": ["run", "rope"], "exercises": []},
    "4": {"text": "Strength training – day 2", "types": ["strength"], "day": 2,
          "exercises": [["Bodyweight squats", "4×25"], ["Lunges with 15 kg", "3×10"]]},
    "5": {"text": "Swimming or skipping rope", "types": ["swim", "rope"], "exercises": []},
    "6": {"text": "Strength training – day 3", "types": ["strength"], "day": 3,
          "exercises": [["Close-grip pull-ups", "4×8"]]},
    "7": {"text": "Active work", "types": ["work"], "exercises": []},
}


def ts(d, h, m=0):
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp())


def days():
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


def build(path, shifted_sleep=False):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO DEVICE VALUES (1, 'Amazfit Helio Strap')")
    con.execute("INSERT INTO USER VALUES (1, 'test', ?, 1)", (int(datetime(1992, 5, 20, tzinfo=TZ).timestamp() * 1000),))
    con.execute("INSERT INTO USER_ATTRIBUTES VALUES (1, 171, 74, NULL, 8000, 0, NULL, 480, 1)")
    rows = []
    for i, d in enumerate(days()):
        wd = d.isoweekday()
        late = shifted_sleep and i % 2 == 1   # alternate nights shifted by 2 hours
        for m in range(1440):
            t = ts(d, 0) + m * 60
            kind, steps, hr, inten = 80, 0, 60, 5
            if (120 if late else 0) <= m < (540 if late else 420):
                kind, hr, inten = 120, 50, 0
            if 540 <= m < 580:                                   # walk, heart rate below the workout threshold
                steps, hr, inten = 100, 92, 120
            if wd in (2, 4, 6):                                  # strength: rope, sets, rope, stretching
                if 1080 <= m < 1090:
                    steps, hr, inten = 130, 125, 150
                elif 1090 <= m < 1120:
                    steps, hr, inten = 5, (140 if (m // 2) % 2 else 112), 110
                elif 1120 <= m < 1135:
                    steps, hr, inten = 130, 132, 150
                elif 1135 <= m < 1142:
                    steps, hr, inten = 0, 95, 20
            if wd == 3 and 1080 <= m < 1110:                     # run
                steps, hr, inten = 160, 165, 200
                if d == date(2026, 3, 18) and m in (1100, 1101):
                    hr = 198                                     # real peak, held for 2 minutes
            if wd == 5 and 450 <= m < 470:                       # rope only
                steps, hr, inten = 130, 135, 150
            if d == date(2026, 3, 11):                           # artefacts not to be taken as max HR
                if m in (720, 721):
                    hr, steps, inten = 212, 0, 5                 # standing still
                if m in (780, 781):
                    hr, steps, inten = 210, 210, 150             # heart rate locked onto the cadence
                if m == 840:
                    hr, steps = 215, 30                          # a single minute
                if m in (180, 181):
                    hr = 214                                     # during sleep
            rows.append((t, 1, 1, inten, steps, kind, hr, 0, 0, 0, 0))
        con.execute("INSERT INTO HUAMI_HEART_RATE_RESTING_SAMPLE VALUES (?,1,1,3600000,?)", (ts(d, 8) * 1000, 54 + i % 3))
        con.execute("INSERT INTO GENERIC_HRV_VALUE_SAMPLE VALUES (?,1,1,?)", (ts(d, 3) * 1000, 60 + i % 3))
    con.executemany("INSERT INTO HUAMI_EXTENDED_ACTIVITY_SAMPLE VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def labels():
    out = {}
    for d in days():
        wd = d.isoweekday()
        if wd in (2, 4, 6) and d != date(2026, 3, 19):          # 19 March stays a suggestion only
            out[str(ts(d, 18))] = {"start": ts(d, 18), "end": ts(d, 19, 1), "type": "strength", "day": {2: 1, 4: 2, 6: 3}[wd],
                                   "load_kg": 0, "features": {}}
        if wd == 5:
            out[str(ts(d, 7, 30))] = {"start": ts(d, 7, 30), "end": ts(d, 7, 49), "type": "rope", "day": None,
                                      "load_kg": 0, "features": {}}
    return out


class Base(unittest.TestCase):
    env = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        build(cls.dir / "gb.db")
        (cls.dir / "plan.json").write_text(json.dumps(PLAN))
        cls.state = cls.dir / "state"
        cls.h = load_app(cls.dir / "gb.db", HELIO_PLAN=str(cls.dir / "plan.json"), HELIO_STATE=str(cls.state),
                         HELIO_NOW=str(NOW), HELIO_STRIDE_M="0.71", **cls.env)

    @classmethod
    def tearDownClass(cls):
        for k in ("HELIO_STATE", "HELIO_PLAN", "HELIO_HRMAX", "HELIO_WEIGHT"):
            os.environ.pop(k, None)
        cls.tmp.cleanup()

    def setUp(self):
        shutil.rmtree(self.state, ignore_errors=True)
        self.state.mkdir(parents=True)
        (self.state / "labels.json").write_text(json.dumps(labels()))
        self.h.clear_caches()

    def day(self, d):
        with closing(self.h.connect()) as con:
            return self.h.compute_day(con, d, self.h.profile(con, d))

    def prof(self, d=END):
        with closing(self.h.connect()) as con:
            return self.h.profile(con, d)


class PersonalHrmax(Base):
    def test_observed_peak_replaces_age_estimate(self):
        p = self.prof()
        self.assertEqual(p["hrmax_pred"], 185)                  # Tanaka: 208 - 0.7 x 33
        self.assertEqual((p["hrmax"], p["hrmax_src"], p["hrmax_obs"]), (198, "observed", 198))
        self.assertEqual(p["hrmax_obs_at"], PEAK_AT)

    def test_artifacts_are_not_peaks(self):
        with closing(self.h.connect()) as con:
            peaks = self.h.hr_peaks(con)
        self.assertNotIn(date(2026, 3, 11), [d for d, (v, _) in peaks.items() if v > 170])
        self.assertEqual(max(v for v, _ in peaks.values()), 198)

    def test_zones_follow_personal_hrmax(self):
        s = self.h.summary(END)
        self.assertEqual(s["profile"]["hrmax"], 198)
        self.assertEqual(s["zone_bounds"], [round(198 * x) for x in self.h.ZONE_PCT])
        self.assertEqual([x["key"] for x in s["hrmax_sources"]], ["tanaka", "mohoric", "bent"])

    def test_peak_below_prediction_keeps_prediction(self):
        h = self.h
        orig = h.hr_peaks
        h.hr_peaks = lambda con: {date(2026, 3, 18): (175, PEAK_AT)}
        try:
            p = self.prof()
            self.assertEqual((p["hrmax"], p["hrmax_src"], p["hrmax_obs"]), (185, "age", 175))
        finally:
            h.hr_peaks = orig

    def test_old_peaks_expire(self):
        h = self.h
        orig = h.hr_peaks
        h.hr_peaks = lambda con: {date(2023, 1, 1): (205, 0)}   # more than 2 years ago
        try:
            self.assertEqual(self.prof()["hrmax_src"], "age")
        finally:
            h.hr_peaks = orig


class FixedHrmax(Base):
    env = {"HELIO_HRMAX": "179"}

    def test_manual_value_wins(self):
        p = self.prof()
        self.assertEqual((p["hrmax"], p["hrmax_src"]), (179, "set"))


class RopeJumps(Base):
    def test_confirmed_strength_session(self):
        x = self.day(date(2026, 3, 17))
        t = next(t for t in x["trainings"] if t["label"])
        self.assertEqual(t["jumps"], 25 * 130)                   # 10 + 15 minutes of rope
        self.assertEqual(x["jumps"], 3250)
        self.assertEqual(x["steps_band"], 40 * 100 + 3250 + 30 * 5)
        self.assertEqual(x["steps"], x["steps_band"] - 3250)
        self.assertAlmostEqual(x["distance_km"], round(x["steps"] * 0.71 / 1000, 2))
        self.assertAlmostEqual(t["distance_km"], round((t["steps"] - 3250) * 0.71 / 1000, 2))

    def test_confirmed_rope_session(self):
        x = self.day(date(2026, 3, 20))
        self.assertEqual(x["jumps"], 20 * 130)
        self.assertEqual(x["steps"], 4000)

    def test_suggestion_only_keeps_steps(self):
        x = self.day(date(2026, 3, 19))                          # strength not confirmed
        t = x["trainings"][0]
        self.assertIsNone(t["label"])
        self.assertEqual(t["suggestion"]["type"], "strength")
        self.assertEqual((x["jumps"], t["jumps"]), (0, 0))

    def test_relabel_as_walk_gives_steps_back(self):
        lb = labels()
        lb[str(ts(date(2026, 3, 17), 18))]["type"] = "walk"
        (self.state / "labels.json").write_text(json.dumps(lb))
        self.assertEqual(self.day(date(2026, 3, 17))["jumps"], 0)

    def test_steps_by_hour_and_health_exclude_jumps(self):
        s = self.h.summary(date(2026, 3, 17))
        self.assertEqual(sum(s["steps_by_hour"]), s["steps"])
        self.assertEqual(s["jumps"], 3250)
        week = self.h.trends(date(2026, 3, 17))["days"]
        self.assertEqual(week[-1]["jumps"], 3250)


class CaloriesAndLoad(Base):
    def test_ranges_contain_the_estimate(self):
        x = self.day(date(2026, 3, 17))
        k = x["kcal"]
        self.assertLessEqual(k["range"][0], k["total"])
        self.assertGreaterEqual(k["range"][1], k["total"])
        self.assertLessEqual(k["active_range"][0], k["active"])
        self.assertGreaterEqual(k["active_range"][1], k["active"])
        bmr = k["bmr"]
        # the band is not narrower than ±10% of basal metabolism
        self.assertLessEqual(k["range"][0], k["total"] - 0.1 * bmr + 1)
        for t in x["trainings"]:
            self.assertLessEqual(t["kcal_range"][0], t["kcal"])
            self.assertGreaterEqual(t["kcal_range"][1], t["kcal"])

    def test_banister_load_per_training_and_day(self):
        import helio_science as sci
        x = self.day(date(2026, 3, 17))
        p = x["profile"]
        for t in x["trainings"]:
            hrs = [hr for _, hr in t["hr"]]
            self.assertEqual(t["trimp"], round(sci.banister_trimp(hrs, p["rest"], p["hrmax"], p["sex"])))
        self.assertEqual(x["trimp"], sum(t["trimp"] for t in x["trainings"]))
        self.assertEqual(self.day(date(2026, 3, 16))["trimp"], 0)       # rest Monday: no workout

    def test_load_status_has_no_ratio_judgement(self):
        t = self.h.trends(END)
        L = t["load"]
        self.assertNotIn("ratio", L)
        self.assertNotIn("status", L)
        self.assertEqual(L["week"], sum(d["trimp"] for d in t["days"]))
        self.assertIsNotNone(L["avg_week"])                      # 21 days of data

    def test_load_needs_14_days(self):
        with closing(self.h.connect()) as con:
            hist = self.h.history(con, date(2026, 3, 12), 28)
        self.assertIsNone(self.h.load_status(hist)["avg_week"])

    def test_readiness_ignores_old_ratio(self):
        x = self.day(END)
        prev = [self.day(END - timedelta(days=i)) for i in range(14, 0, -1)]
        p = x["profile"]
        a = self.h.readiness(x, prev, None, None, p)
        b = self.h.readiness(x, prev, None, {"ratio": 2.0, "status": "molto alto"}, p)
        self.assertEqual(a, b)


class Vo2max(Base):
    def test_estimate(self):
        v = self.h.summary(END)["vo2max"]
        self.assertEqual(v["value"], round(15.3 * 198 / 55, 1))
        self.assertEqual((v["low"], v["high"]), (round(v["value"] - 7, 1), round(v["value"] + 7, 1)))
        self.assertEqual(v["met"], round(v["value"] / 3.5, 1))
        self.assertEqual(v["hrmax_src"], "observed")
        self.assertEqual(len(v["series"]), 12)
        self.assertEqual(v["series"][-1], [END.isoformat(), v["value"]])
        # no default altitude goal: it appears only when set in the profile
        self.assertNotIn("altitude", v)
        self.h.save_profile({"profile": {"altitude_goal_m": 4000, "altitude_goal_name": "Test summit"}})
        try:
            a = self.h.summary(END)["vo2max"]["altitude"]
        finally:
            self.h.save_profile({"reset": True})
        self.assertEqual((a["m"], a["name"]), (4000, "Test summit"))
        import helio_science as sci
        self.assertEqual(a["value"], round(v["value"] * sci.altitude_fraction(4000), 1))
        self.assertEqual(a["fraction"], round(sci.altitude_fraction(4000), 2))
        self.assertTrue(a["frac_low"] < a["fraction"] < a["frac_high"])
        self.assertTrue(a["extrapolated"])

    def test_not_enough_resting_values(self):
        with closing(self.h.connect()) as con:
            v, rest, n = self.h.vo2max_for(con, date(2026, 3, 3), 198)
        self.assertEqual((v, n), (None, 2))
        self.assertIsNone(self.h.summary(date(2026, 3, 3))["vo2max"]["value"])

    def test_calories_use_the_estimate(self):
        p = self.prof()
        self.assertEqual(p["vo2max"], p["vo2max_est"])


class Regularity(Base):
    def test_regular_sleep_scores_100(self):
        r = self.h.summary(END)["sri"]
        self.assertEqual(r["value"], 100)
        self.assertEqual(r["prev"], 100)                         # 23 February-8 March: 7 days of data are enough
        self.assertEqual(r["onset"], {"mean": 0, "sd": 0})
        self.assertEqual(r["wake"], {"mean": 420, "sd": 0})
        self.assertGreaterEqual(r["pairs_h"], r["need_h"])

    def test_shifted_sleep_scores_lower(self):
        build(self.dir / "late.db", shifted_sleep=True)
        orig = self.h.DB
        self.h.DB = str(self.dir / "late.db")
        try:
            with closing(self.h.connect()) as con:
                r = self.h.sleep_regularity(con, END)
        finally:
            self.h.DB = orig
        self.assertEqual(r["value"], round(200 * (1440 - 240) / 1440 - 100))
        self.assertGreater(r["onset"]["sd"], 30)

    def test_not_enough_hours(self):
        with closing(self.h.connect()) as con:
            r = self.h.sleep_regularity(con, date(2026, 3, 4))
        self.assertIsNone(r["value"])
        self.assertLess(r["pairs_h"], r["need_h"])


class Advice(Base):
    def base(self, status, avg7=55.0, rhr="normal", n=20):
        return {"hrv": {"mean": 61.0, "low": 58.0, "high": 64.0, "avg7": avg7, "status": status, "n_base": n, "need": 14},
                "rhr": {"status": rhr}}

    def plan(self, *types, rest=False):
        return {"text": "x", "types": list(types), "day": 1 if "strength" in types else None, "rest": rest}

    def test_decision_table(self):
        p = self.prof()
        A = self.h.daily_advice
        self.assertEqual(A(self.plan(rest=True), self.base("normal"), p)["status"], "rest")
        self.assertIsNone(A(self.plan(), self.base("normal"), p))
        self.assertEqual(A(self.plan("work"), self.base("below"), p)["status"], "work")
        self.assertEqual(A(self.plan("run"), {"hrv": {"mean": None, "n_base": 5, "need": 14}}, p)["status"], "collecting")
        self.assertEqual(A(self.plan("run"), {"hrv": {"mean": 60, "avg7": None}}, p)["status"], "collecting")
        self.assertEqual(A(self.plan("run"), self.base("normal"), p)["status"], "follow")
        self.assertEqual(A(self.plan("run"), self.base("above"), p)["status"], "follow")
        easy = A(self.plan("run", "rope"), self.base("below"), p)
        self.assertEqual((easy["status"], T(easy["title"])), ("light", "Easy version today"))
        self.assertIn(f"below {round(198 * 0.7)} bpm", T(easy["text"]))
        strength = A(self.plan("strength"), self.base("below"), p)
        self.assertEqual(T(strength["title"]), "Do the session, with less volume")
        self.assertIn("bittencourt", [s["key"] for s in strength["sources"]])

    def test_resting_hr_mentioned(self):
        a = self.h.daily_advice(self.plan("run"), self.base("normal", rhr="above"), self.prof())
        self.assertIn("resting heart rate is also above", T(a["text"]))

    def test_sources(self):
        a = self.h.daily_advice(self.plan("run"), self.base("normal"), self.prof())
        self.assertEqual([s["key"] for s in a["sources"]], ["ais_hrv", "vesterinen", "granero"])

    def test_in_summary(self):
        self.assertEqual(self.h.summary(END)["advice"]["status"], "work")             # Sunday: active work
        self.assertEqual(self.h.summary(date(2026, 3, 17))["advice"]["status"], "follow")  # stable HRV
        self.assertEqual(self.h.summary(date(2026, 3, 16))["advice"]["status"], "rest")


class Week(Base):
    def test_current_week_on_sunday(self):
        w = self.h.week_summary(END)
        self.assertEqual((w["start"], w["end"], w["which"], w["complete"]), ("2026-03-16", "2026-03-22", "current", False))
        st = [x["status"] for x in w["current"]["days"]]
        self.assertEqual(st, ["rest", "done", "done", "done", "done", "done", "today"])  # Sunday is today
        c = w["current"]
        self.assertEqual((c["plan_done"], c["plan_total"]), (5, 5))
        self.assertEqual(c["strength_days"], 3)
        self.assertEqual(c["sleep_avg"], 420)
        self.assertEqual(c["nights"], 7)
        self.assertEqual(c["rhr_avg"], round(sum(54 + i % 3 for i in range(14, 21)) / 7, 1))
        self.assertEqual(c["sri"], 100)
        self.assertEqual(c["jumps"], 3250 * 2 + 2600)            # Tuesday and Saturday confirmed + Friday's rope
        self.assertEqual(c["data_days"], 7)

    def test_past_work_day_without_shift(self):
        w = self.h.week_summary(END, "previous")
        self.assertEqual(w["current"]["days"][-1]["status"], "missing")

    def test_monday_shows_last_week(self):
        w = self.h.week_summary(date(2026, 3, 16))
        self.assertEqual((w["start"], w["end"], w["which"]), ("2026-03-09", "2026-03-15", "previous"))
        self.assertTrue(w["complete"])

    def test_explicit_choice(self):
        self.assertEqual(self.h.week_summary(END, "previous")["start"], "2026-03-09")
        self.assertEqual(self.h.week_summary(date(2026, 3, 16), "current")["start"], "2026-03-16")

    def test_diff_with_previous_week(self):
        w = self.h.week_summary(END)
        self.assertIn("zone_minutes", w["diff"])
        self.assertEqual(w["diff"]["sleep_avg"], 0)
        self.assertEqual(w["diff"]["zone_minutes"], w["current"]["zone_minutes"] - w["previous"]["zone_minutes"])

    def test_no_diff_without_previous_data(self):
        w = self.h.week_summary(date(2026, 3, 4))                # first week: no previous week
        self.assertEqual(w["previous"]["data_days"], 0)
        self.assertEqual(w["diff"], {})

    def test_work_shift_counts_when_inserted(self):
        lb = labels()
        lb[f"m{ts(END, 9)}"] = {"start": ts(END, 9), "end": ts(END, 17), "type": "work", "manual": True,
                               "load_kg": 0, "features": {}}
        (self.state / "labels.json").write_text(json.dumps(lb))
        self.assertEqual(self.h.week_summary(END)["current"]["days"][-1]["status"], "done")

    def test_targets_and_sources(self):
        w = self.h.week_summary(END)
        self.assertEqual(w["targets"]["strength_days"], 2)
        self.assertEqual(w["targets"]["zone_week"], 150)
        self.assertEqual({s["key"] for s in w["sources"]}, {"who", "momma", "cappuccio", "paluch", "chaput"})
        json.dumps(w)


class StrengthLog(Base):
    TUE = date(2026, 3, 17)

    def test_exercises_from_the_plan(self):
        sd = self.h.strength_day(self.TUE)                       # Tuesday: strength day 1 confirmed
        names = [e["name"] for e in sd["exercises"]]
        self.assertEqual(names, ["Wide-grip pull-ups", "Parallel bar dips", "Plank"])  # rope is not logged
        kinds = {e["name"]: e["kind"] for e in sd["exercises"]}
        self.assertEqual(kinds, {"Wide-grip pull-ups": "system", "Parallel bar dips": "system", "Plank": "time"})
        self.assertEqual(sd["strength_day_numbers"], [1, 2, 3])
        self.assertEqual(sd["body_weight"], 74)

    def test_strength_day_done_on_another_weekday(self):
        sd = self.h.strength_day(END, 2)                         # Sunday, but logging day 2
        self.assertEqual([e["name"] for e in sd["exercises"]], ["Bodyweight squats", "Lunges with 15 kg"])
        self.assertEqual(sd["exercises"][1]["default_kg"], 15.0)
        self.assertEqual(self.h.strength_day(END)["exercises"], [])  # Sunday without strength: no exercises

    def test_save_and_progress(self):
        import helio_science as sci
        entries = [{"name": "Wide-grip pull-ups", "sets": [{"reps": 8, "kg": 0}, {"reps": 6, "kg": 5}]},
                   {"name": "Plank", "sets": [{"sec": 60}, {"sec": 45}]},
                   {"name": "Parallel bar dips", "sets": []}]
        self.assertEqual(self.h.save_strength(self.TUE, entries), {"ok": True})
        saved = json.loads((self.state / "strength.json").read_text())
        self.assertEqual([e["name"] for e in saved["2026-03-17"]["entries"]], ["Wide-grip pull-ups", "Plank"])
        sd = self.h.strength_day(self.TUE)
        prog = {p["name"]: p for p in sd["progress"]}
        pull = prog["Wide-grip pull-ups"]["sessions"][-1]
        best = max(sci.brzycki_1rm(74, 8), sci.brzycki_1rm(79, 6))
        self.assertEqual(pull["e1rm"], round(best, 1))
        self.assertEqual(pull["e1rm_added"], round(best - 74, 1))
        self.assertEqual((pull["sets"], pull["reps"]), (2, 14))
        self.assertEqual(prog["Plank"]["sessions"][-1]["sec"], 105)
        self.assertEqual(sd["week"], {"days": 1, "sets": 4, "volume": 30.0, "hold": 0.0})   # 6 reps × 5 kg
        nxt = self.h.strength_day(date(2026, 3, 19), 1)
        self.assertEqual(nxt["exercises"][0]["last"]["sets"][0]["reps"], 8)   # prefilled from last time

    def test_body_weight_from_the_log_is_used(self):
        self.h.save_weight(date(2026, 3, 1), 80)
        self.h.save_strength(self.TUE, [{"name": "Wide-grip pull-ups", "sets": [{"reps": 5, "kg": 0}]}])
        import helio_science as sci
        pull = {p["name"]: p for p in self.h.strength_day(self.TUE)["progress"]}["Wide-grip pull-ups"]["sessions"][-1]
        self.assertEqual(pull["e1rm"], round(sci.brzycki_1rm(80, 5), 1))

    def test_loaded_reps_exercise_has_volume_not_1rm(self):
        self.h.save_strength(date(2026, 3, 19), [{"name": "Lunges with 15 kg", "sets": [{"reps": 10, "kg": 15}] * 3}])
        s = {p["name"]: p for p in self.h.strength_day(date(2026, 3, 19))["progress"]}["Lunges with 15 kg"]["sessions"][-1]
        self.assertEqual((s["volume"], s["e1rm"]), (450.0, None))

    def test_too_many_reps_no_1rm(self):
        self.h.save_strength(self.TUE, [{"name": "Wide-grip pull-ups", "sets": [{"reps": 12, "kg": 0}]}])
        s = self.h.strength_day(self.TUE)["progress"][0]["sessions"][-1]
        self.assertIsNone(s["e1rm"])                             # Reynolds 2006: not above 10 reps

    def test_empty_entries_delete_the_day(self):
        self.h.save_strength(self.TUE, [{"name": "Plank", "sets": [{"sec": 30}]}])
        self.h.save_strength(self.TUE, [])
        self.assertEqual(self.h.strength_load(), {})

    def test_validation(self):
        bad = [
            [{"name": "", "sets": [{"reps": 5}]}],
            [{"name": "x" * 81, "sets": [{"reps": 5}]}],
            [{"name": "A", "sets": [{"reps": 201}]}],
            [{"name": "A", "sets": [{"reps": -1}]}],
            [{"name": "A", "sets": [{"reps": 5, "kg": 301}]}],
            [{"name": "A", "sets": [{"sec": 3601}]}],
            [{"name": "A", "sets": [{"reps": True}]}],
            [{"name": "A", "sets": [{"reps": "8"}]}],
            [{"name": "A", "sets": [{"reps": float("nan")}]}],
            [{"name": "A", "sets": ["8x10"]}],
            [{"name": "A", "sets": [{"reps": 5}] * 16}],
            [{"name": "A", "sets": [{"reps": 5}]}] * 26,
            "tutto",
        ]
        for entries in bad:
            with self.assertRaises(ValueError, msg=str(entries)[:60]):
                self.h.save_strength(self.TUE, entries)
        with self.assertRaises(ValueError):
            self.h.save_strength(END + timedelta(days=1), [{"name": "A", "sets": [{"reps": 5}]}])
        self.assertFalse((self.state / "strength.json").exists())

    def test_exercise_kind(self):
        k = self.h.exercise_kind
        # names are typed by the user: Italian and English words are both recognised
        for name, kind in (("Pull-up presa stretta", "system"), ("Trazioni alla sbarra", "system"), ("Dips su parallele", "system"),  # lang: it
                           ("Chin-up", "system"), ("Plank laterale", "time"), ("Corda, riscaldamento", "skip"),  # lang: it
                           ("Stretching finale", "skip"), ("Squat con salto", "reps"), ("Affondi con zavorra 15 kg", "reps"),  # lang: it
                           ("Close-grip pull-ups", "system"), ("Side plank hold", "time"), ("Rope warm-up", "skip"),
                           ("Jump squats", "reps")):
            self.assertEqual(k(name), kind, name)
        self.assertEqual(self.h.default_kg("Lunges with 15 kg"), 15.0)
        self.assertEqual(self.h.default_kg("Backpack 7,5 kg"), 7.5)
        self.assertEqual(self.h.default_kg("Squat"), 0.0)


class PlanEditing(Base):
    def test_payload_from_file(self):
        pl = self.h.plan_payload()
        self.assertEqual(pl["source"], "file")
        self.assertEqual(sorted(pl["plan"]), [str(i) for i in range(1, 8)])
        self.assertEqual(pl["plan"]["2"]["day"], 1)
        self.assertEqual(len(pl["types"]), len(self.h.ACTIVITY_TYPES))

    def test_save_changes_the_day_plan_and_caches(self):
        before = self.h.summary(date(2026, 3, 17))["plan"]["text"]
        new = {k: dict(v) for k, v in PLAN.items()}
        new["2"] = {"text": "Edited strength", "types": ["strength"], "day": 1, "exercises": [["Pull-ups", "5×5"]]}
        self.assertEqual(self.h.save_plan({"plan": new}), {"ok": True})
        self.assertEqual(self.h.plan_payload()["source"], "app")
        s = self.h.summary(date(2026, 3, 17))
        self.assertNotEqual(before, s["plan"]["text"])
        self.assertEqual((s["plan"]["text"], s["plan"]["exercises"]), ("Edited strength", [["Pull-ups", "5×5"]]))
        self.assertFalse((self.state / "plan.tmp").exists())
        self.assertEqual(self.h.save_plan({"reset": True}), {"ok": True})
        self.assertEqual(self.h.plan_payload()["source"], "file")
        self.assertEqual(self.h.summary(date(2026, 3, 17))["plan"]["text"], before)

    def test_normalisation(self):
        out = self.h.validate_plan({"1": "Rest", "3": {"text": " Run ", "types": ["run", "run"], "day": 2}})
        self.assertEqual(out["1"], {"text": "Rest", "types": [], "day": None, "exercises": []})
        self.assertEqual(out["3"]["types"], ["run"])
        self.assertIsNone(out["3"]["day"])                       # the strength day applies only with strength
        self.assertEqual(out["3"]["text"], "Run")
        self.assertEqual(out["7"]["types"], [])

    def test_validation(self):
        bad = [
            "plan", [], {"2": 5}, {"2": {"text": "x" * 121}}, {"2": {"types": ["volo"]}}, {"2": {"types": "strength"}},
            {"2": {"types": ["strength"], "day": 4}}, {"2": {"types": ["strength"], "day": True}},
            {"2": {"exercises": [["x", "y"]] * 21}}, {"2": {"exercises": ["Pull-ups"]}}, {"2": {"exercises": [["", "x"]]}},
            {"2": {"exercises": [["x" * 61, ""]]}}, {"2": {"exercises": [["a", "b", "c"]]}}, {"2": {"exercises": [[1, 2]]}},
            {"2": {"types": ["run"] * 5}},
        ]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)[:60]):
                self.h.validate_plan(b)
        self.assertFalse((self.state / "plan.json").exists())


class WeightLog(Base):
    def test_weight_applies_from_its_date(self):
        self.h.save_weight(date(2026, 3, 16), 79.0)
        before, after = self.prof(date(2026, 3, 15)), self.prof(date(2026, 3, 16))
        self.assertEqual((before["weight"], before["weight_src"]), (74, "Gadgetbridge"))
        self.assertEqual((after["weight"], after["weight_src"], after["weight_date"]), (79.0, "log", "2026-03-16"))
        self.assertEqual(after["bmr"], round(10 * 79 + 6.25 * 171 - 5 * 33 + 5))  # Mifflin-St Jeor
        self.assertEqual(self.day(date(2026, 3, 16))["kcal"]["bmr"], after["bmr"])
        self.assertEqual(self.day(date(2026, 3, 15))["kcal"]["bmr"], before["bmr"])
        self.assertEqual(self.h.weight_payload()["entries"], [["2026-03-16", 79.0]])

    def test_latest_entry_wins_and_delete(self):
        self.h.save_weight(date(2026, 3, 10), 81.0)
        self.h.save_weight(date(2026, 3, 20), 80.2)
        self.assertEqual(self.prof(date(2026, 3, 19))["weight"], 81.0)
        self.assertEqual(self.prof(END)["weight"], 80.2)
        self.h.save_weight(date(2026, 3, 20), None)
        self.assertEqual(self.prof(END)["weight"], 81.0)
        self.assertEqual(self.h.weight_for(date(2026, 3, 9)), None)

    def test_validation(self):
        for kg in (29.9, 250.1, "80", True, float("nan"), -5):
            with self.assertRaises(ValueError, msg=str(kg)):
                self.h.save_weight(date(2026, 3, 16), kg)
        with self.assertRaises(ValueError):
            self.h.save_weight(END + timedelta(days=1), 80)
        with self.assertRaises(ValueError):
            self.h.save_weight(date(1999, 12, 31), 80)
        self.assertFalse((self.state / "weights.json").exists())

    def test_corrupt_file_is_ignored(self):
        (self.state / "weights.json").write_text('{"2026-03-16": "tanti", "ieri": 70, "2026-03-17": 78}')
        self.assertEqual(self.h.weights_load(), {"2026-03-17": 78.0})


class Export(Base):
    def parse(self, text, fmt="eu"):
        self.assertTrue(text.startswith("﻿") == (fmt == "eu"))
        return list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";" if fmt == "eu" else ","))

    def test_days(self):
        rows = self.parse(self.h.export_csv("days", date(2026, 3, 16), END))
        head, body = rows[0], rows[1:]
        self.assertEqual(head[:4], ["date", "steps", "rope_jumps", "km"])
        self.assertEqual(len(body), 7)
        tue = dict(zip(head, body[1]))
        self.assertEqual((tue["date"], tue["rope_jumps"]), ("2026-03-17", "3250"))
        self.assertIn(",", tue["km"])                             # decimal comma in the "it" (semicolon) format
        self.assertEqual(tue["vo2max_est"].replace(",", "."), str(self.prof(date(2026, 3, 17))["vo2max_est"]))
        self.assertEqual(tue["resting_hr_source"], "band")
        it = self.parse(self.h.export_csv("days", date(2026, 3, 16), END, "eu", "it"))
        self.assertEqual(it[0][:2], [self.h.tr("it", "csv.col.date"), self.h.tr("it", "csv.col.steps")])

    def test_standard_format(self):
        rows = self.parse(self.h.export_csv("days", date(2026, 3, 17), date(2026, 3, 17), "std"), "std")
        tue = dict(zip(rows[0], rows[1]))
        self.assertIn(".", tue["km"])
        self.assertNotIn(",", tue["km"])

    def test_trainings(self):
        rows = self.parse(self.h.export_csv("workouts", date(2026, 3, 16), END))
        head, body = rows[0], rows[1:]
        types = [dict(zip(head, r))["type"] for r in body]
        self.assertEqual(types, ["Strength training", "Running", "Strength training", "Rope skipping",
                                 "Strength training"])
        self.assertEqual(head[:4], ["date", "start", "end", "type"])
        first = dict(zip(head, body[0]))
        # the start can be a few minutes early: Gadgetbridge sessions widen the block
        self.assertTrue("17:50" <= first["start"] <= "18:00", first["start"])
        self.assertEqual(first["confirmed"], "yes")
        self.assertTrue(int(first["kcal_min"]) <= int(first["kcal_active"]) <= int(first["kcal_max"]))

    def test_strength_and_formula_injection(self):
        self.h.save_strength(date(2026, 3, 17), [{"name": "=HYPERLINK(\"x\")", "sets": [{"reps": 5, "kg": 2.5}]},
                                                 {"name": "Wide-grip pull-ups", "sets": [{"reps": 8, "kg": 0}]}])
        text = self.h.export_csv("strength", date(2026, 3, 16), END)
        rows = self.parse(text)
        self.assertEqual(rows[0][:4], ["date", "exercise", "set", "reps"])
        self.assertEqual(rows[1][1], "'=HYPERLINK(\"x\")")      # no formulas run by the spreadsheet
        self.assertEqual(rows[1][4], "2,5")
        self.assertEqual(rows[2][6].replace(",", "."), str(round(74 * 36 / 29, 1)))

    def test_cells(self):
        c = self.h.csv_cell
        self.assertEqual(c(None, "eu"), "")
        self.assertEqual(c(True, "eu"), "yes")
        self.assertEqual(c(True, "eu", "it"), self.h.tr("it", "csv.yes"))
        self.assertEqual(c(3.50, "eu"), "3,5")
        self.assertEqual(c(3.0, "std"), "3")
        self.assertEqual(c(12, "eu"), "12")
        self.assertEqual(c("a;b", "eu"), '"a;b"')
        self.assertEqual(c("a,b", "std"), '"a,b"')
        self.assertEqual(c('by "Zepp"', "eu"), '"by ""Zepp"""')   # quotes doubled as the CSV format requires
        self.assertEqual(c('x;"y"', "eu"), '"x;""y"""')
        for danger in ("=1+1", "+1", "-1", "@SUM(A1)"):
            self.assertTrue(c(danger, "eu").startswith("'"), danger)

    def test_limits(self):
        with self.assertRaises(ValueError):
            self.h.export_csv("days", date(2024, 1, 1), END)
        with self.assertRaises(ValueError):
            self.h.export_csv("password", START, END)
        rows = self.parse(self.h.export_csv("days", END, date(2026, 3, 20)))   # swapped dates: it swaps them back
        self.assertEqual([r[0] for r in rows[1:]], ["2026-03-20", "2026-03-21", "2026-03-22"])


class Http(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.srv = cls.h.ThreadingHTTPServer(("127.0.0.1", 0), cls.h.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=20) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def post(self, path, body, headers=None):
        hd = {"Content-Type": "application/json", "X-Helio": "1", **(headers or {})}
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=hd, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_get_endpoints(self):
        for path in ("/api/week?date=2026-03-22", "/api/week?date=2026-03-22&which=previous", "/api/week?which=strano",
                     "/api/strength?date=2026-03-17", "/api/strength?date=2026-03-22&day=2", "/api/strength?day=9",
                     "/api/plan", "/api/weight"):
            code, body, _ = self.get(path)
            self.assertEqual(code, 200, path)
            json.loads(body)

    def test_csv_download(self):
        code, body, headers = self.get("/api/export.csv?kind=days&from=2026-03-16&to=2026-03-22")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="helio-days-2026-03-16_2026-03-22.csv"')
        self.assertTrue(body.decode().startswith("\ufeffdate;steps"))  # English unless lang=it
        self.assertEqual(self.get("/api/export.csv?kind=nothing")[0], 400)
        self.assertEqual(self.get("/api/export.csv?kind=days&from=2020-01-01&to=2026-03-22")[0], 400)
        code, body, _ = self.get("/api/export.csv?kind=workouts&fmt=std&from=2026-03-17&to=2026-03-17")
        self.assertTrue(body.decode().startswith("date,start"))
        code, body, _ = self.get("/api/export.csv?kind=workouts&fmt=std&lang=it&from=2026-03-17&to=2026-03-17")
        self.assertTrue(body.decode().startswith(self.h.tr("it", "csv.col.date") + ","))

    def test_csv_download_with_old_link_codes(self):
        # links built by versions up to 259 (legacy codes): same file, English name
        code, body, headers = self.get("/api/export.csv?kind=allenamenti&fmt=it&from=2026-03-17&to=2026-03-17")  # legacy
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="helio-workouts-2026-03-17_2026-03-17.csv"')
        self.assertTrue(body.decode().startswith("\ufeffdate;start"))

    def test_post_endpoints(self):
        self.assertEqual(self.post("/api/weight", {"date": "2026-03-20", "kg": 80.5}), (200, {"ok": True}))
        self.assertEqual(json.loads(self.get("/api/weight")[1])["current"]["kg"], 80.5)
        self.assertEqual(self.post("/api/strength", {"date": "2026-03-17", "entries": [{"name": "Plank", "sets": [{"sec": 60}]}]}),
                         (200, {"ok": True}))
        plan = json.loads(self.get("/api/plan")[1])["plan"]
        plan["1"]["text"] = "Active rest"
        self.assertEqual(self.post("/api/plan", {"plan": plan}), (200, {"ok": True}))
        self.assertEqual(json.loads(self.get("/api/plan")[1])["plan"]["1"]["text"], "Active rest")
        self.assertEqual(self.post("/api/plan", {"reset": True}), (200, {"ok": True}))

    def test_post_validation_messages(self):
        cases = [
            ("/api/weight", {"date": "ieri", "kg": 80}, "invalid date"),
            ("/api/weight", {"date": "2026-03-20", "kg": 400}, "invalid weight"),
            ("/api/weight", {"date": "2030-01-01", "kg": 80}, "future"),
            ("/api/strength", {"date": "2026-03-17", "entries": [{"name": "A", "sets": [{"reps": 999}]}]}, "repetitions"),
            ("/api/strength", {"entries": []}, "invalid date"),
            ("/api/plan", {"plan": {"2": {"types": ["volo"]}}}, "activity types"),
            ("/api/plan", {"plan": "niente"}, "invalid plan"),
            ("/api/weight", [1, 2], "invalid request"),
        ]
        for path, body, msg in cases:
            code, res = self.post(path, body)
            self.assertEqual(code, 400, (path, body))
            self.assertIn(msg, T(res["error"]), (path, body))

    def test_post_needs_header_and_size_limit(self):
        self.assertEqual(self.post("/api/plan", {"reset": True}, {"X-Helio": ""})[0], 403)
        self.assertEqual(self.post("/api/weight", {"date": "2026-03-20", "kg": 80, "pad": "x" * 2000})[0], 400)
        self.assertEqual(self.post("/api/plan", {"reset": True, "pad": "x" * 40000})[0], 400)  # above 32 KB


if __name__ == "__main__":
    unittest.main(verbosity=2)
