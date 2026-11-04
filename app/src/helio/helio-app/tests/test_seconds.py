"""Heart rate of every second (Helio Strap, type 0x57) and SDNN measured by the band (type 0x4e), as the Helio build of
Gadgetbridge saves them (GENERIC_HEART_RATE_SAMPLE, GENERIC_HRV_SDNN_SAMPLE, timestamps in ms): zones split by seconds
where they make the count more precise, recovery at 60 s after a workout, SDNN of the night as a measurement only.
All data here is synthetic, built on the confirmed formats; no real recording.

The test database (test_helio.build_db): max heart rate 185, so the zones start at 92.5, 111, 129.5, 148 and 166.5 bpm;
a run from 18:00 to 18:19 at 165 bpm (zone index 3) with 160 steps a minute, then 60 bpm and no steps.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
import urllib.error
import urllib.request
import threading
from pathlib import Path

from test_helio import D0, DAY, build_db, load_app

GB_TABLES = """
CREATE TABLE "GENERIC_HEART_RATE_SAMPLE" ("TIMESTAMP" INTEGER NOT NULL, "DEVICE_ID" INTEGER NOT NULL,
  "USER_ID" INTEGER NOT NULL, "HEART_RATE" INTEGER NOT NULL, PRIMARY KEY ("TIMESTAMP", "DEVICE_ID") ON CONFLICT REPLACE);
CREATE TABLE "GENERIC_HRV_SDNN_SAMPLE" ("TIMESTAMP" INTEGER NOT NULL, "DEVICE_ID" INTEGER NOT NULL,
  "USER_ID" INTEGER NOT NULL, "VALUE" INTEGER NOT NULL, PRIMARY KEY ("TIMESTAMP", "DEVICE_ID") ON CONFLICT REPLACE);
"""
RUN = D0 + 18 * 3600          # 18:00, first minute of the run
RUN_END = RUN + 20 * 60       # 18:20: end of the last minute above the training threshold


def seconds(con, start, values, device=1):
    con.executemany('INSERT INTO GENERIC_HEART_RATE_SAMPLE VALUES (?,?,1,?)',
                    [((start + i) * 1000, device, v) for i, v in enumerate(values) if v is not None])


class Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop("HELIO_HRMAX", None)  # the zones below assume the estimate from age (185)
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "gb.db"
        build_db(self.db)
        with sqlite3.connect(self.db) as con:
            con.executescript(GB_TABLES)

    def tearDown(self):
        os.environ.pop("HELIO_STATE", None)
        self.tmp.cleanup()

    def write(self, fn):
        with sqlite3.connect(self.db) as con:
            fn(con)

    def app(self, past=False):
        env = {"HELIO_STATE": str(Path(self.tmp.name) / "state")}
        if past:  # DAY is yesterday: its results come from the caches, checked against the rows they read
            env["HELIO_NOW"] = str(D0 + 86400 + 12 * 3600)
        return load_app(self.db, **env)

    def run_training(self, s):
        (t,) = [x for x in s["trainings"] if x["start"] < RUN_END and x["end"] >= RUN]
        return t


class Zones(Base):
    def test_without_seconds_nothing_changes(self):
        s = self.app().summary(DAY)
        self.assertEqual(s["zones_sec_min"], 0)
        t = self.run_training(s)
        self.assertEqual(t["zones"][3], 20)
        self.assertEqual(t["zones_sec_min"], 0)
        self.assertIsNone(t["hrr60"])
        # and without the tables at all (another band, an older Gadgetbridge)
        tmp = Path(self.tmp.name) / "plain.db"
        build_db(tmp)
        s2 = load_app(tmp, HELIO_STATE=str(Path(self.tmp.name) / "state2")).summary(DAY)
        self.assertEqual((s2["zones"], s2["zone_minutes"]), (s["zones"], s["zone_minutes"]))

    def test_seconds_split_a_minute_between_zones(self):
        # minute 18:05: 30 s at 160 (zone index 3), 30 s at 170 (index 4); the other run minutes at 165 every second
        def fill(con):
            for m in range(20):
                vals = [160] * 30 + [170] * 30 if m == 5 else [165] * 60
                seconds(con, RUN + m * 60, vals)
        self.write(fill)
        s = self.app().summary(DAY)
        t = self.run_training(s)
        self.assertEqual(t["zones_sec_min"], 20)
        h = self.app()
        with closing(h.connect()) as con:
            sec = h.sec_minutes(con, RUN, RUN_END, 185)
        self.assertEqual(sec[RUN + 300], (60, [0, 0, 0, 30, 30], 30 * 160 + 30 * 170, 170))
        rows = [{"t": RUN + m * 60, "hr": 165} for m in range(20)]
        zf, by_sec, below = h.mixed_zones(rows, sec, 185)
        self.assertEqual((by_sec, below), (20, 0))
        self.assertAlmostEqual(zf[3], 19.5)
        self.assertAlmostEqual(zf[4], 0.5)
        # WHO minutes from the fractions: 19.5 x 2 + 0.5 x 2
        self.assertEqual(h.who_minutes(zf), 40)
        self.assertEqual(s["zones_sec_min"], 20)

    def test_too_few_seconds_or_a_removed_minute_keep_the_old_rule(self):
        h = self.app()
        sec = {RUN: (44, [0, 0, 0, 0, 44]), RUN + 60: (45, [0, 0, 0, 0, 45]), RUN + 120: (60, [0, 0, 0, 0, 60])}
        rows = [{"t": RUN, "hr": 165}, {"t": RUN + 60, "hr": 165}, {"t": RUN + 120, "hr": None, "hr_fix": "picco"}]
        zf, by_sec, _ = h.mixed_zones(rows, sec, 185)
        self.assertEqual(by_sec, 1)                            # only the minute with 45 seconds
        self.assertEqual([round(z, 3) for z in zf], [0, 0, 0, 1, 1])  # 44 s: the minute value; removed: nothing
        # a minute without a minute value but with its seconds counts by them
        zf, by_sec, _ = h.mixed_zones([{"t": RUN + 60, "hr": None}], sec, 185)
        self.assertEqual((by_sec, zf[4]), (1, 1.0))

    def test_zone_thresholds_and_invalid_seconds(self):
        # exactly on a threshold goes up, as zone_of; 0 and 255 are not readings
        self.write(lambda con: seconds(con, RUN, [148, 147, 166, 167, 0, 255, 92, 93]))
        h = self.app()
        with closing(h.connect()) as con:
            n, z, total, mx = h.sec_minutes(con, RUN, RUN + 60, 185)[RUN]
        self.assertEqual(n, 6)
        self.assertEqual(z, [1, 0, 1, 2, 1])  # 93 | - | 147 | 148, 166 | 167
        self.assertEqual((total, mx), (148 + 147 + 166 + 167 + 92 + 93, 167))  # 92: a reading, below the zones
        self.assertEqual(h.zone_of(148, 185), 3)
        self.assertEqual(h.zone_of(167, 185), 4)

    def test_other_devices_rows_are_ignored(self):
        self.write(lambda con: seconds(con, RUN, [170] * 60, device=2))
        h = self.app()
        with closing(h.connect()) as con:
            self.assertEqual(h.sec_minutes(con, RUN, RUN + 60, 185), {})

    def test_a_late_sync_of_seconds_recomputes_a_past_day(self):
        before = self.app(past=True).summary(DAY)
        self.assertEqual(before["zones_sec_min"], 0)
        self.write(lambda con: [seconds(con, RUN + m * 60, [170] * 60) for m in range(20)])
        h = self.app(past=True)
        after = h.summary(DAY)
        self.assertEqual(after["zones_sec_min"], 20)
        z = h.zone_of(170, after["profile"]["hrmax"])
        self.assertNotEqual(z, h.zone_of(165, after["profile"]["hrmax"]))  # the seconds really move the minutes
        self.assertEqual(self.run_training(after)["zones"][z], 20)


class Recovery(Base):
    def fill(self, end_hr=165, at60=135, after_valid=11):
        def fn(con):
            for m in range(20):
                seconds(con, RUN + m * 60, [165] * 60)
            vals = [end_hr] * 60
            seconds(con, RUN_END - 60, vals)               # last minute of the run
            rec = [150] * 120
            for i in range(55, 66):
                rec[i] = at60 if i - 55 < after_valid else None
            seconds(con, RUN_END, rec)
        self.write(fn)

    def test_hrr60_from_the_seconds(self):
        self.fill()
        s = self.app().summary(DAY)
        r = self.run_training(s)["hrr60"]
        self.assertEqual(r, {"value": 30, "end_hr": 165, "hr60": 135, "at": RUN_END, "active": False})
        self.assertIn([self.run_training(s)["start"], 30], s["hrr_recent"])
        self.assertEqual([x["key"] for x in s["hrr_sources"]], ["cole_hrr", "qiu_hrr", "fecchio_hrr"])
        # the sources say why no threshold is used: none of them is tied to a company, each states its conflicts
        self.assertTrue(all(x["coi"] in ("none", "not_declared") for x in s["hrr_sources"]))

    def test_needs_enough_seconds_on_both_sides(self):
        self.fill(after_valid=7)
        self.assertIsNone(self.run_training(self.app().summary(DAY))["hrr60"])

    def test_not_after_an_easy_end(self):
        # the last seconds below the training threshold (40% of the heart rate reserve): not the end of an exercise
        self.fill(end_hr=100, at60=90)
        self.assertIsNone(self.run_training(self.app().summary(DAY))["hrr60"])

    def test_walking_in_the_recovery_minute_is_flagged(self):
        # (walking after a run makes the workout longer, as before: here only the flag of the recovery minute)
        self.fill()
        h = self.app()
        rows = [{"t": RUN_END, "steps": 90}, {"t": RUN_END + 60, "steps": 0}]
        with closing(h.connect()) as con:
            self.assertTrue(h.hrr60(con, RUN_END, 120, rows)["active"])
            self.assertFalse(h.hrr60(con, RUN_END, 120, [{"t": RUN_END, "steps": 59}])["active"])

    def test_end_of_the_exercise(self):
        h = self.app()
        t = {"start": RUN, "end": RUN_END - 60, "source": "battito"}
        self.assertEqual(h.training_end(t, []), RUN_END)
        self.assertEqual(h.training_end({**t, "source": "manual", "end": RUN_END + 30}, []), RUN_END + 30)
        band = [{"start": RUN + 10, "end": RUN_END - 25, "source": "band"},
                {"start": RUN - 7200, "end": RUN - 3600, "source": "band"},          # another workout, earlier
                {"start": RUN, "end": RUN_END, "source": "gadgetbridge"}]           # a walk session: not a stop
        self.assertEqual(h.training_end(t, band), RUN_END - 25)


ROPE = D0 + 16 * 3600      # 16:00-16:20 skipping rope (130 jumps a minute, 160 bpm), then walking until 16:30


def rope_then_walk(con):
    for m in range(30):
        steps, hr = (130, 160) if m < 20 else (60, 110)
        con.execute("UPDATE HUAMI_EXTENDED_ACTIVITY_SAMPLE SET STEPS=?, HEART_RATE=?, RAW_INTENSITY=150 WHERE TIMESTAMP=?",
                    (steps, hr, ROPE + m * 60))
    for m in range(20):
        seconds(con, ROPE + m * 60, [150] * 20 + [175] * 10 + [160] * 30)    # peaks the minute value smooths out
    seconds(con, ROPE + 1200, [140] * 55 + [128] * 11 + [115] * 534)          # the walk, 16:20-16:30
    seconds(con, ROPE + 1800, [100] * 60)
    seconds(con, ROPE - 180, [62] * 180)                      # the session starts at 15:57 (Gadgetbridge's session)


class Corrections(Base):
    def rope_training(self, s):
        (t,) = [x for x in s["trainings"] if x["start"] <= ROPE + 600 < x["end"]]
        return t

    def test_heart_rate_of_the_workout_from_the_seconds(self):
        self.write(rope_then_walk)
        t = self.rope_training(self.app().summary(DAY))
        self.assertEqual(t["max_hr"], 175)                   # the minute values say 160
        # the average weighted by seconds: 3 minutes before, 20 minutes of rope, 10 of walking (seconds 140/128/115)
        self.assertEqual((t["start"], t["end"]), (ROPE - 180, ROPE + 1740))
        before = 180 * 62
        rope = 20 * (20 * 150 + 10 * 175 + 30 * 160)
        walk = 55 * 140 + 11 * 128 + 534 * 115
        self.assertEqual(t["avg_hr"], round((before + rope + walk) / (180 + 20 * 60 + 600)))
        h = self.app()
        rows = [{"t": 0, "hr": 100}, {"t": 60, "hr": 120, "hr_fix": None}, {"t": 120, "hr": None, "hr_fix": "picco"}]
        sec = {60: (50, [0] * 5, 50 * 130, 190), 120: (60, [0] * 5, 60 * 200, 210)}
        self.assertEqual(h.mixed_hr(rows, sec), (round((60 * 100 + 50 * 130) / 110), 190))  # a removed minute stays out
        self.assertEqual(h.mixed_hr(rows, {}), (110, 120))   # without seconds: the minute values, as before
        self.assertEqual(h.mixed_hr([], sec), (None, None))

    def test_without_seconds_the_old_values(self):
        def fn(con):
            for m in range(30):
                steps, hr = (130, 160) if m < 20 else (60, 110)
                con.execute("UPDATE HUAMI_EXTENDED_ACTIVITY_SAMPLE SET STEPS=?, HEART_RATE=?, RAW_INTENSITY=150 "
                            "WHERE TIMESTAMP=?", (steps, hr, ROPE + m * 60))
        self.write(fn)
        t = self.rope_training(self.app().summary(DAY))
        # (the 3 minutes at 60 bpm before 16:00 belong to the session)
        self.assertEqual((t["max_hr"], t["avg_hr"]), (160, round((3 * 60 + 20 * 160 + 10 * 110) / 33)))

    def test_recovery_after_the_rope_when_the_session_ends_walking(self):
        self.write(rope_then_walk)
        t = self.rope_training(self.app().summary(DAY))
        self.assertEqual(t["rope"][-1][1], ROPE + 1200)      # the rope recognised until 16:20
        self.assertGreater(t["end"], ROPE + 1200)            # the session goes on walking
        r = t["hrr60"]
        self.assertEqual((r["at"], r["after"]), (ROPE + 1200, "rope"))
        self.assertEqual((r["end_hr"], r["hr60"], r["value"]), (160, 128, 32))
        self.assertTrue(r["active"])                         # walking in that minute: said too

    def test_rope_until_the_end_is_the_normal_case(self):
        # the session ends with the rope: the usual end, no "after"
        def fn(con):
            for m in range(20):
                con.execute("UPDATE HUAMI_EXTENDED_ACTIVITY_SAMPLE SET STEPS=130, HEART_RATE=160, RAW_INTENSITY=150 "
                            "WHERE TIMESTAMP=?", (ROPE + m * 60,))
                seconds(con, ROPE + m * 60, [160] * 60)
            seconds(con, ROPE + 1200, [140] * 55 + [125] * 11)
        self.write(fn)
        r = self.rope_training(self.app().summary(DAY))["hrr60"]
        self.assertEqual(r["at"], ROPE + 1200)
        self.assertNotIn("after", r)

    def test_minutes_below_the_zones_close_the_sum(self):
        self.write(rope_then_walk)
        t = self.rope_training(self.app().summary(DAY))
        # every minute has its seconds: zones and minutes below them add up to the minutes split by seconds
        self.assertEqual(t["zones_sec_min"], 33)
        self.assertEqual(sum(t["zones"]) + t["zone_below"], 33)
        self.assertEqual(t["zone_below"], 3)                  # 15:57-16:00 at 62 bpm
        h = self.app()
        rows = [{"t": 0, "hr": 80}, {"t": 60, "hr": 120}]
        sec = {60: (60, [10, 10, 0, 0, 0], 60 * 90, 120)}       # 40 s below the zones
        zf, by_sec, below = h.mixed_zones(rows, sec, 185)
        self.assertAlmostEqual(below, 1 + 40 / 60)
        parts = h.round_parts([below] + zf)
        self.assertEqual(sum(parts), 2)
        self.assertEqual(parts, [2, 0, 0, 0, 0, 0])
        for vals in ([0.4, 0.4, 0.2], [1.5, 1.5, 0.0], [10.33, 20.33, 30.34], [0.0, 0.0]):
            self.assertEqual(sum(h.round_parts(vals)), round(sum(vals)), vals)


class Retention(Base):
    """Gadgetbridge deletes the seconds older than the kept period, only up to where Ritmo saved their results first
    (seconds_retention): an old day keeps the same values after the pruning. The deletion is simulated here as
    HelioSecondsRetention does it (rows of the device before the limit)."""
    LATER = 100  # days after DAY: with 90 days kept, DAY is pruned

    def app_at(self, days_after, **env):
        env = {"HELIO_STATE": str(Path(self.tmp.name) / "state"), "HELIO_NOW": str(D0 + days_after * 86400 + 12 * 3600), **env}
        return load_app(self.db, **env)

    def fill_day(self, start=RUN):
        def fn(con):
            for m in range(20):
                seconds(con, start + m * 60, [160] * 30 + [170] * 30 if m == 5 else [165] * 60)
            seconds(con, start + 20 * 60, [150] * 55 + [135] * 11 + [120] * 10)
        self.write(fn)

    def prune(self, before):
        self.write(lambda con: con.execute("DELETE FROM GENERIC_HEART_RATE_SAMPLE WHERE DEVICE_ID=1 AND TIMESTAMP<?",
                                           (before * 1000,)))

    def shown(self, s):
        t = self.run_training(s)
        return {"zones": s["zones"], "zone_minutes": s["zone_minutes"], "zones_sec_min": s["zones_sec_min"],
                "hr_minutes": s["hr_minutes"],
                "t_zones": t["zones"], "t_sec": t["zones_sec_min"], "hrr60": t["hrr60"], "t_below": t["zone_below"],
                "t_avg": t["avg_hr"], "t_max": t["max_hr"]}

    def test_old_day_keeps_its_values_after_the_pruning(self):
        self.fill_day()
        h = self.app_at(self.LATER)
        before = self.shown(h.summary(DAY))
        self.assertEqual(before["zones_sec_min"], 21)
        self.assertGreater(before["hr_minutes"], 21)          # the whole day's minutes with a heart rate
        self.assertEqual(before["hrr60"]["value"], 30)
        res = h.seconds_retention()
        self.assertEqual(res["before"], h.midnight(DAY + timedelta(days=self.LATER - 90)))
        self.assertEqual((res["keep"], res["derived"], res["device_id"]), (90, 1, 1))
        saved = json.loads((Path(self.tmp.name) / "state" / "seconds-2026.json").read_text())
        self.assertEqual(set(saved), {DAY.isoformat()})
        self.prune(res["before"])
        h = self.app_at(self.LATER)
        with closing(h.connect()) as con:
            self.assertFalse(h.day_has_seconds(con, DAY))
        self.assertEqual(self.shown(h.summary(DAY)), before)
        # the saved results travel with the data export
        import helio_transfer as ht
        self.assertIn("seconds-2026.json", ht.state_files(Path(self.tmp.name) / "state"))

    def test_without_saved_results_the_values_would_change(self):
        # the reason for saving them: the same pruning without seconds_retention loses the per-second values
        self.fill_day()
        self.prune(D0 + 86400)
        s = self.app_at(self.LATER).summary(DAY)
        self.assertEqual(s["zones_sec_min"], 0)
        self.assertIsNone(self.run_training(s)["hrr60"])

    def test_kept_days_and_always_are_not_pruned(self):
        self.fill_day()
        h = self.app_at(30)                                  # DAY inside the 90 days: not derived
        res = h.seconds_retention()
        self.assertEqual(res["derived"], 0)
        self.assertLess(res["before"], D0)                   # the limit falls 60 days before DAY: DAY stays whole
        h.save_settings({"hr_seconds_keep": None})
        res = self.app_at(self.LATER).seconds_retention()
        self.assertEqual((res["before"], res["derived"]), (0, 0))

    def test_limit_per_run(self):
        self.fill_day()
        self.fill_day(RUN + 86400)                           # the next day too
        h = self.app_at(self.LATER)
        res = h.seconds_retention(limit=1)
        self.assertEqual(res["derived"], 1)
        self.assertEqual(res["before"], D0 + 86400)          # only up to the first day not derived yet
        res = h.seconds_retention()
        self.assertEqual(res["derived"], 2)

    def test_a_new_max_heart_rate_uses_the_minute_values(self):
        self.fill_day()
        h = self.app_at(self.LATER)
        res = h.seconds_retention()
        self.prune(res["before"])
        s = self.app_at(self.LATER, HELIO_HRMAX="200").summary(DAY)
        os.environ.pop("HELIO_HRMAX", None)
        self.assertEqual(s["zones_sec_min"], 0)

    def test_setting(self):
        h = self.app()
        self.assertEqual(h.settings_load()["hr_seconds_keep"], 90)
        for v in (30, 365, None, 90):
            self.assertEqual(h.save_settings({"hr_seconds_keep": v})["hr_seconds_keep"], v)
        for bad in (60, 0, True, "90", 90.5):
            with self.assertRaises(h.UserError):
                h.save_settings({"hr_seconds_keep": bad})
        self.assertEqual(h.settings_load()["hr_seconds_keep"], 90)


class SecondsApi(Base):
    def get(self, h, path):
        srv = h.ThreadingHTTPServer(("127.0.0.1", 0), h.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}{path}", timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_series_with_gaps(self):
        self.write(lambda con: seconds(con, RUN, [150, None, 152, 0, 154]))
        code, r = self.get(self.app(), f"/api/seconds?start={RUN}&end={RUN + 6}")
        self.assertEqual(code, 200)
        self.assertEqual(r, {"start": RUN, "values": [150, None, 152, None, 154, None]})
        # no seconds at all: an empty list (the page keeps the minute chart)
        code, r = self.get(self.app(), f"/api/seconds?start={RUN - 7200}&end={RUN - 7100}")
        self.assertEqual(r["values"], [])

    def test_bad_intervals_are_refused(self):
        h = self.app()
        for q in (f"start={RUN}&end={RUN}", f"start={RUN}&end={RUN + 6 * 3600 + 1}", "start=x&end=1", "end=5", ""):
            code, _ = self.get(h, f"/api/seconds?{q}")
            self.assertEqual(code, 400, q)


class Sdnn(Base):
    def test_night_median_only_inside_the_night(self):
        def fn(con):
            for t, v in ((D0 + 2 * 3600, 48), (D0 + 4 * 3600, 62), (D0 + 6 * 3600, 55),
                         (D0 + 5 * 3600, 0), (D0 + 5 * 3600 + 60, 400),        # not plausible: left out
                         (D0 + 12 * 3600, 90)):                                 # midday: not the night
                con.execute("INSERT INTO GENERIC_HRV_SDNN_SAMPLE VALUES (?,1,1,?)", (t * 1000, v))
        self.write(fn)
        s = self.app().summary(DAY)
        self.assertEqual((s["sleep"]["sdnn"], s["sleep"]["sdnn_n"]), (55, 3))
        self.assertEqual([x["key"] for x in s["sdnn_sources"]], ["xu_prv", "nunan_hrv"])
        # only a measurement: no baseline, no status, no insight is built on it
        self.assertNotIn("sdnn", s["baselines"] or {})
        self.assertNotIn("sdnn", json.dumps(s.get("insights") or []))

    def test_no_sdnn_without_records(self):
        s = self.app().summary(DAY)
        self.assertIsNone(s["sleep"]["sdnn"])
        self.assertEqual(s["sleep"]["sdnn_n"], 0)

    def test_a_late_sdnn_sync_recomputes_a_past_night(self):
        self.assertIsNone(self.app(past=True).summary(DAY)["sleep"]["sdnn"])
        self.write(lambda con: con.execute("INSERT INTO GENERIC_HRV_SDNN_SAMPLE VALUES (?,1,1,51)", ((D0 + 3 * 3600) * 1000,)))
        self.assertEqual(self.app(past=True).summary(DAY)["sleep"]["sdnn"], 51)


if __name__ == "__main__":
    unittest.main()
