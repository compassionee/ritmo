"""Tests of the comments (insights), the key points of the night and the vital signs with a personal range.

The thresholds are those of the cited sources: NSF 2015 (duration 7-9 hours), NSF 2017 (latency up to 30 min,
wake up to 20 min, efficiency at least 85%), Ohayon 2004 (stages), AIS (personal range of heart rate and HRV).
"""
from contextlib import closing
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from datetime import timedelta
from pathlib import Path

from test_helio import T, D0, DAY, build_db, load_app


def night(**kw):
    sl = {"total": 450, "deep": 80, "rem": 100, "light": 270, "awake": 15, "latency": 10, "efficiency": 92,
          "awakenings": 3, "score": 80}
    sl.update(kw)
    return sl


class AppBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "gb.db"
        build_db(db)
        cls.h = load_app(db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class SleepHighlights(AppBase):
    def hl(self, **kw):
        return {x["key"]: x for x in self.h.sleep_highlights(night(**kw))}

    def test_good_night_all_ideal(self):
        res = self.hl()
        self.assertEqual(set(res), {"duration", "latency", "waso", "efficiency", "deep", "rem"})
        self.assertTrue(all(x["ok"] for x in res.values()), res)

    def test_nsf_thresholds(self):
        self.assertTrue(self.hl(total=420)["duration"]["ok"])
        self.assertTrue(self.hl(total=540)["duration"]["ok"])
        self.assertFalse(self.hl(total=419)["duration"]["ok"])
        self.assertFalse(self.hl(total=541)["duration"]["ok"])
        self.assertTrue(self.hl(latency=30)["latency"]["ok"])
        self.assertFalse(self.hl(latency=31)["latency"]["ok"])
        self.assertTrue(self.hl(awake=20)["waso"]["ok"])
        self.assertFalse(self.hl(awake=21)["waso"]["ok"])
        self.assertTrue(self.hl(efficiency=85)["efficiency"]["ok"])
        self.assertFalse(self.hl(efficiency=84)["efficiency"]["ok"])

    def test_stages_are_informative_not_alarming(self):
        res = self.hl(deep=30, rem=140, light=280, total=450)  # 7% deep, 31% REM
        for k in ("deep", "rem"):
            self.assertFalse(res[k]["ok"])
            self.assertEqual((res[k]["level"], T(res[k]["label"])), (2, "Outside the typical range"))

    def test_every_highlight_cites_sources(self):
        for x in self.h.sleep_highlights(night()):
            self.assertTrue(x["sources"])
            self.assertTrue(all(s["url"].startswith("https://") for s in x["sources"]))
        keys = {T(s["title"]) for x in self.h.sleep_highlights(night()) for s in x["sources"]}
        self.assertTrue(any("Ohayon" in k for k in keys))

    def test_none(self):
        self.assertEqual(self.h.sleep_highlights(None), [])
        self.assertIsNone(self.h.sleep_insight(None))


class SleepInsight(AppBase):
    def title(self, **kw):
        return T(self.h.sleep_insight(night(**kw))["title"])

    def test_titles(self):
        self.assertEqual(self.title(), "A good night")
        self.assertEqual(self.title(total=330), "A short night")
        self.assertEqual(self.title(awakenings=8), "A fragmented night")
        self.assertEqual(self.title(awake=46), "A fragmented night")
        self.assertEqual(self.title(awake=25), "An average night")
        self.assertEqual(self.title(latency=40), "An average night")

    def test_text_has_numbers(self):
        t = T(self.h.sleep_insight(night(awakenings=9, awake=60))["text"])
        self.assertIn("9 times", t)
        self.assertIn("1h 00m", t)


def fake_summary(**kw):
    s = {"hr": {"resting": 55}, "baselines": {"rhr": {"mean": None}, "hrv": {"status": "collecting"}},
         "workouts": [], "active_min": 20, "steps": 3000, "zone_minutes": 5, "zone_minutes_week": 40,
         "goals": {"steps": 8000, "zone_week": 150}, "sleep": None, "quality": {"hr_fixes": []}}
    s.update(kw)
    return s


class DayInsights(AppBase):
    def test_quiet_day_has_no_comments(self):
        self.assertEqual(self.h.day_insights(fake_summary()), [])

    def test_rhr_above_band_comes_first(self):
        s = fake_summary(hr={"resting": 62}, baselines={"rhr": {"mean": 54, "low": 53, "high": 55},
                                                        "hrv": {"status": "normal"}},
                         steps=9000)
        out = self.h.day_insights(s)
        self.assertEqual(out[0]["tone"], "warn")
        self.assertIn("above your range", T(out[0]["title"]))
        self.assertIn("53–55", T(out[0]["text"]))

    def test_hrv_below(self):
        s = fake_summary(baselines={"rhr": {"mean": None}, "hrv": {"status": "below", "avg7": 41, "low": 50, "high": 62}})
        out = self.h.day_insights(s)
        self.assertEqual(T(out[0]["title"]), "HRV below your normal")

    def test_goals_and_big_day(self):
        s = fake_summary(active_min=180, steps=12182, zone_minutes=211, zone_minutes_week=211)
        titles = [T(x["title"]) for x in self.h.day_insights(s)]
        self.assertIn("A great day of movement!", titles)
        self.assertIn("WHO weekly goal reached", titles)
        self.assertIn("Step goal reached", titles)
        msg = self.h.day_insights(s)[0]["text"]
        text = T(msg)
        self.assertIn("12,182 steps", text)
        self.assertIn("12.182", T(msg, "it"))  # Italian thousands separator

    def test_single_workout(self):
        w = {"name": "Walk", "duration_min": 35, "avg_hr": 112, "max_hr": 133, "kcal": 244, "trimp": 46}
        out = self.h.day_insights(fake_summary(workouts=[w]))
        self.assertEqual(T(out[0]["title"]), "Walk, 35m")

    def test_sensor_fixes_reported(self):
        out = self.h.day_insights(fake_summary(quality={"hr_fixes": [{"t": 1, "v": 190, "why": "picco"}]}))
        self.assertEqual(out[-1]["tag"], "data")

    def test_summary_has_comments(self):
        s = self.h.summary(DAY)
        json.dumps(s)
        self.assertIsInstance(s["insights"], list)
        self.assertEqual(T(s["sleep_insight"]["title"]), T(self.h.sleep_insight(s["sleep"])["title"]))
        self.assertEqual(len(s["sleep_highlights"]), 6)


class Vitals(unittest.TestCase):
    """Personal resting heart rate range with 30 days of synthetic values."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "gb.db"
        build_db(cls.db)
        with closing(sqlite3.connect(cls.db)) as con:
            con.execute("DELETE FROM HUAMI_HEART_RATE_RESTING_SAMPLE")
            for i in range(1, 30):
                v = 53 if i % 2 else 55
                con.execute("INSERT INTO HUAMI_HEART_RATE_RESTING_SAMPLE VALUES (?,1,1,3600000,?)",
                            ((D0 - i * 86400 + 8 * 3600) * 1000, v))
            for i in range(0, 7):  # last week higher
                con.execute("UPDATE HUAMI_HEART_RATE_RESTING_SAMPLE SET HEART_RATE=60 WHERE TIMESTAMP=?",
                            ((D0 - i * 86400 + 8 * 3600) * 1000,))
            con.execute("INSERT INTO HUAMI_HEART_RATE_RESTING_SAMPLE VALUES (?,1,1,3600000,60)", ((D0 + 8 * 3600) * 1000,))
            con.commit()
        cls.h = load_app(cls.db)
        cls.v = cls.h.vitals(DAY)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_structure(self):
        self.assertEqual(set(self.v), {"rhr", "hrv", "resp", "spo2", "temp"})
        for k, x in self.v.items():
            self.assertEqual(len(x["series"]), 30, k)
            self.assertEqual(x["series"][-1][0], DAY.isoformat())
            for f in ("title", "unit", "decimals", "last", "mean", "low", "high", "status", "n_base", "need"):
                self.assertIn(f, x)
        json.dumps(self.v)

    def test_rhr_band_and_status(self):
        r = self.v["rhr"]
        self.assertGreaterEqual(r["n_base"], 14)
        self.assertLess(r["low"], r["mean"])
        self.assertLess(r["mean"], r["high"])
        self.assertEqual(r["status"], "above")  # 7-day average at 60, above the range
        self.assertEqual(r["last"], [DAY.isoformat(), 60] if isinstance(r["last"], list) else (DAY.isoformat(), 60))

    def test_other_vitals_collecting(self):
        for k in ("resp", "spo2", "temp"):
            self.assertEqual(self.v[k]["status"], "collecting")
            self.assertIsNone(self.v[k]["low"])

    def test_http_endpoint(self):
        srv = self.h.ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/api/vitals?end={DAY.isoformat()}"
            with urllib.request.urlopen(url) as r:
                data = json.loads(r.read())
            self.assertEqual(data["rhr"]["status"], "above")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ReadinessUsesBandValues(unittest.TestCase):
    def test_estimates_not_mixed_with_band_values(self):
        import helio_app as h
        p = {"sleep_goal_min": 480}
        day = {"resting": 57, "resting_src": "band"}
        prev = [{"resting": 62, "resting_src": "estimate"}, {"resting": 53, "resting_src": "band"},
                {"resting": 58, "resting_src": "band"}]
        self.assertIsNone(h.readiness(day, prev, None, None, p))  # only 2 band values
        prev.append({"resting": 54, "resting_src": "band"})
        r = h.readiness(day, prev, None, None, p)
        self.assertEqual(r["baseline"], 54)  # median of 53, 58, 54 (without the estimate 62)
        self.assertTrue(any("+3" in x for x in T(r["parts"])))
