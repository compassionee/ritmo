"""Helio tests. Standard library only: run them with

    python3 -m unittest discover -s /opt/helio-app/tests -v

Tests on synthetic data build a database with the same structure as Gadgetbridge's
and check every calculation against known values. If the real database exists (HELIO_LIVE_DB),
the internal consistency of your data is checked too.
"""
import base64
from contextlib import closing
import importlib
import json
import os
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))
TZ = ZoneInfo("Europe/Rome")
DAY = date(2026, 3, 10)  # a Tuesday without a clock change
D0 = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ).timestamp())
LIVE_DB = os.environ.get("HELIO_LIVE_DB", "")

SCHEMA = """
CREATE TABLE DEVICE (_id INTEGER PRIMARY KEY, NAME TEXT);
CREATE TABLE USER (_id INTEGER PRIMARY KEY, NAME TEXT, BIRTHDAY INTEGER, GENDER INTEGER);
CREATE TABLE USER_ATTRIBUTES (_id INTEGER PRIMARY KEY, HEIGHT_CM INTEGER, WEIGHT_KG INTEGER, SLEEP_GOAL_HPD INTEGER,
  STEPS_GOAL_SPD INTEGER, VALID_FROM_UTC INTEGER, VALID_TO_UTC INTEGER, SLEEP_GOAL_MPD INTEGER, USER_ID INTEGER);
CREATE TABLE HUAMI_EXTENDED_ACTIVITY_SAMPLE ("TIMESTAMP" INTEGER NOT NULL, "DEVICE_ID" INTEGER NOT NULL,
  "USER_ID" INTEGER NOT NULL, "RAW_INTENSITY" INTEGER NOT NULL, "STEPS" INTEGER NOT NULL, "RAW_KIND" INTEGER NOT NULL,
  "HEART_RATE" INTEGER NOT NULL, "UNKNOWN1" INTEGER, "SLEEP" INTEGER, "DEEP_SLEEP" INTEGER, "REM_SLEEP" INTEGER,
  PRIMARY KEY ("TIMESTAMP", "DEVICE_ID")) WITHOUT ROWID;
CREATE TABLE HUAMI_SLEEP_SESSION_SAMPLE (TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, DATA BLOB);
CREATE TABLE HUAMI_HEART_RATE_RESTING_SAMPLE (TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, UTC_OFFSET INTEGER, HEART_RATE INTEGER);
CREATE TABLE HUAMI_STRESS_SAMPLE (TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, TYPE_NUM INTEGER, STRESS INTEGER);
CREATE TABLE HUAMI_SPO2_SAMPLE (TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, TYPE_NUM INTEGER, SPO2 INTEGER);
CREATE TABLE HUAMI_PAI_SAMPLE (TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, UTC_OFFSET INTEGER, PAI_LOW REAL,
  PAI_MODERATE REAL, PAI_HIGH REAL, TIME_LOW INTEGER, TIME_MODERATE INTEGER, TIME_HIGH INTEGER, PAI_TODAY REAL, PAI_TOTAL REAL);
CREATE TABLE HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE (TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, UTC_OFFSET INTEGER, RATE INTEGER);
CREATE TABLE GENERIC_HRV_VALUE_SAMPLE (TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, VALUE INTEGER);
CREATE TABLE GENERIC_TEMPERATURE_SAMPLE (TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, TEMPERATURE REAL,
  TEMPERATURE_TYPE INTEGER, TEMPERATURE_LOCATION INTEGER);
CREATE TABLE BATTERY_LEVEL (TIMESTAMP INTEGER, DEVICE_ID INTEGER, LEVEL INTEGER, BATTERY_INDEX INTEGER);
"""


def hm(h, m, day=DAY):
    return int(datetime(day.year, day.month, day.day, h, m, tzinfo=TZ).timestamp())


def sleep_blob(day, stages, bed=(None, None), score=70, written=None):
    """Builds a sleep summary in the same format as the band.
    stages: list of (start_minute, inclusive_end_minute, code) from midnight of the previous day."""
    day0 = int(datetime(day.year, day.month, day.day, tzinfo=TZ).timestamp())
    b = bytearray(594)
    struct.pack_into("<II", b, 0, written or day0 + 9 * 3600, day0)
    b[8], b[9] = 1, 1
    s0, s1 = stages[0][0], stages[-1][1] + 1
    struct.pack_into("<4H", b, 10, s0, s1, bed[0] or s0, bed[1] or s1)
    b[22] = score
    struct.pack_into("<H", b, 84, len(stages))
    tot = {4: 0, 5: 0, 7: 0, 8: 0}
    for k, (s, e, t) in enumerate(stages):
        struct.pack_into("<HHB", b, 86 + 5 * k, s, e, t)
        tot[t] += e - s + 1
    struct.pack_into("<4H", b, 586, tot[8], tot[4], tot[5], tot[7])
    return bytes(b)


def build_db(path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO DEVICE VALUES (1, 'Amazfit Helio Strap')")
    born = int(datetime(1992, 5, 20, tzinfo=TZ).timestamp() * 1000)
    con.execute("INSERT INTO USER VALUES (1, 'test', ?, 1)", (born,))
    con.execute("INSERT INTO USER_ATTRIBUTES VALUES (1, 171, 74, NULL, 8000, 0, NULL, 480, 1)")
    rows = []
    # night 01:00-07:00 with sleep kind, resting day with HR 60 and two walks
    for m in range(0, 24 * 60):
        t = D0 + m * 60
        kind, steps, hr, inten = 80, 0, 60, 5
        if 60 <= m < 420:
            kind, hr = 120, 50
        # walk A 09:00-09:29: 100 steps/min, HR 120
        if 540 <= m < 570:
            steps, hr, inten = 100, 120, 120
        # short break 09:30-09:32 then again 09:33-09:39 (same session)
        if 573 <= m < 580:
            steps, hr, inten = 90, 118, 110
        # run 18:00-18:19: 160 steps/min
        if 1080 <= m < 1100:
            steps, hr, inten = 160, 165, 200
        # isolated minute with few steps: must not create sessions
        if m == 1200:
            steps = 30
        # band not worn 20:30-20:59
        if 1230 <= m < 1260:
            kind, hr = 115, 255
        rows.append((t, 1, 1, inten, steps, kind, hr, 0, 0, 0, 0))
    con.executemany("INSERT INTO HUAMI_EXTENDED_ACTIVITY_SAMPLE VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    # band sleep: from 01:00 (1500 minutes from the previous day) to 07:00
    stages = [(1500, 1529, 4), (1530, 1589, 5), (1590, 1649, 4), (1650, 1679, 8), (1680, 1689, 7),
              (1690, 1799, 4), (1800, 1859, 8)]
    old = sleep_blob(DAY, stages[:3], score=40, written=D0 + 3 * 3600)
    new = sleep_blob(DAY, stages, bed=(1480, 1865), score=82, written=D0 + 7 * 3600)
    con.execute("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)", (D0 * 1000, bytes(594)))
    con.execute("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)", ((D0 + 3 * 3600) * 1000, old))
    con.execute("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)", ((D0 + 7 * 3600) * 1000, new))
    con.execute("INSERT INTO HUAMI_HEART_RATE_RESTING_SAMPLE VALUES (?,1,1,3600000,52)", ((D0 + 8 * 3600) * 1000,))
    for i, v in enumerate([20, 30, 45, 50, 65, 85]):
        con.execute("INSERT INTO HUAMI_STRESS_SAMPLE VALUES (?,1,1,1,?)", ((D0 + 12 * 3600 + i * 300) * 1000, v))
    for i, v in enumerate([99, 97, 94, 98]):
        con.execute("INSERT INTO HUAMI_SPO2_SAMPLE VALUES (?,1,1,0,?)", ((D0 + 2 * 3600 + i * 300) * 1000, v))
    con.execute("INSERT INTO HUAMI_PAI_SAMPLE VALUES (?,1,1,3600000,2,20,15,100,120,15,37.4,88.6)", ((D0 + 20 * 3600) * 1000,))
    for i, v in enumerate([60, 70, 80, 90, 200]):
        con.execute("INSERT INTO GENERIC_HRV_VALUE_SAMPLE VALUES (?,1,1,?)", ((D0 + 2 * 3600 + i * 60) * 1000, v))
    for i, v in enumerate([14, 15, 16]):
        con.execute("INSERT INTO HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE VALUES (?,1,1,3600000,?)", ((D0 + 3 * 3600 + i * 60) * 1000, v))
    con.execute("INSERT INTO GENERIC_TEMPERATURE_SAMPLE VALUES (?,1,1,33.5,2,9)", ((D0 + 3 * 3600) * 1000,))
    con.execute("INSERT INTO BATTERY_LEVEL VALUES (?,1,77,0)", (D0 + 21 * 3600,))
    con.commit()
    con.close()


def T(v, lang="en"):
    """Text of a server message ({"$": key, ...}) as the page would show it (English by default); anything else unchanged."""
    h = sys.modules["helio_app"]
    if isinstance(v, dict) and "$" in v:
        return h.tr(lang, v["$"], **{k: x for k, x in v.items() if k != "$"})
    if isinstance(v, list):
        return [T(x, lang) for x in v]
    return v


def load_app(db, **env):
    os.environ.update({"HELIO_DB": str(db), "HELIO_PLAN": "/nonexistent", "HELIO_STRIDE_M": "0.72", "HELIO_TZ": "Europe/Rome",
                       "HELIO_NOW": str(D0 + 23 * 3600 + 59 * 60)})
    for k in ("HELIO_HEIGHT", "HELIO_WEIGHT", "HELIO_AGE", "HELIO_SEX", "HELIO_TLS_BIND", "HELIO_PASSWORD",
              "HELIO_LAN_OPEN", "HELIO_CATALOG", "HELIO_CATALOG_BUNDLED"):
        os.environ.pop(k, None)
    os.environ.update({k: str(v) for k, v in env.items()})
    import helio_app
    return importlib.reload(helio_app)


class SyntheticData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "gb.db"
        build_db(cls.db)
        cls.h = load_app(cls.db)
        cls.s = cls.h.summary(DAY)
        cls.t = cls.h.trends(DAY)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_profile(self):
        p = self.s["profile"]
        self.assertEqual((p["age"], p["sex"], p["weight"], p["height"]), (33, "M", 74, 171))
        self.assertEqual(p["hrmax"], 185)
        self.assertEqual(p["bmr"], round(10 * 74 + 6.25 * 171 - 5 * 33 + 5))
        self.assertEqual(p["missing"], [])

    def test_steps_and_distance(self):
        expected = 30 * 100 + 7 * 90 + 20 * 160 + 30
        self.assertEqual(self.s["steps"], expected)
        self.assertAlmostEqual(self.s["distance_km"], round(expected * 0.72 / 1000, 2))

    def test_sessions_like_gadgetbridge(self):
        w = [x for x in self.s["workouts"] if x["source"] == "gadgetbridge"]
        self.assertEqual(len(w), 2, w)
        walk, run = w
        # the 3-minute break does not split the walk; the session ends at the last active minute
        self.assertEqual((walk["start"], walk["end"]), (hm(9, 0), hm(9, 39)))
        self.assertEqual(walk["steps"], 30 * 100 + 7 * 90)
        self.assertEqual(T(walk["name"]), "Walking")
        self.assertEqual(T(run["name"]), "Running")
        # as in Gadgetbridge: the session can start up to 5 minutes before the first active minute
        # and the mean HR includes those minutes
        self.assertTrue(hm(17, 55) <= run["start"] <= hm(18, 0), run["start"])
        self.assertEqual(run["end"], hm(18, 19))
        self.assertTrue(140 <= run["avg_hr"] <= 165, run["avg_hr"])
        self.assertEqual(self.s["active_min"], round(((hm(9, 39) - hm(9, 0)) + (run["end"] - run["start"])) / 60))
        self.assertEqual(self.s["active_steps"], walk["steps"] + run["steps"])

    def test_heart_rate(self):
        hr = self.s["hr"]
        self.assertEqual(hr["min"], 50)
        self.assertEqual(hr["max"], 165)
        self.assertEqual(hr["resting"], 52)
        self.assertEqual(hr["resting_src"], "band")
        self.assertNotIn(255, [v for _, v in hr["series"]])

    def test_sleep_uses_latest_band_summary(self):
        sl = self.s["sleep"]
        self.assertEqual(sl["source"], "band")
        self.assertEqual(sl["score"], 82)
        self.assertEqual((sl["deep"], sl["rem"], sl["awake"]), (60, 90, 10))
        self.assertEqual(sl["light"], 30 + 60 + 110)
        self.assertEqual(sl["total"], 60 + 90 + 200)
        self.assertEqual((sl["start"], sl["end"]), (hm(1, 0), hm(7, 0)))
        self.assertEqual(sl["latency"], 20)
        self.assertEqual(sl["in_bed"], 1865 - 1480)
        self.assertEqual(sl["efficiency"], round(100 * 350 / 385))
        self.assertEqual(sl["hrv"], 80)  # median, not affected by the outlier 200
        self.assertEqual(sl["resp"], 15.0)
        # minimum confirmed by two consecutive readings; the isolated 94 is still counted as a dip
        self.assertEqual((sl["spo2"], sl["spo2_min"], sl["spo2_min_raw"], sl["spo2_dips"]), (97, 97, 94, 1))
        self.assertEqual(sl["temp"], 33.5)
        self.assertEqual(sum(round((b - a) / 60) for a, b, s in sl["timeline"] if s != "awake"), sl["total"])

    def test_sleep_fallback_from_samples(self):
        with closing(sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)) as con:
            raw = self.h.sleep_from_samples(con, DAY)
        self.assertEqual((raw["start"], raw["end"]), (hm(1, 0), hm(7, 0)))

    def test_stress_spo2_pai_battery(self):
        st = self.s["stress"]
        self.assertEqual(st["avg"], round((20 + 30 + 45 + 50 + 65 + 85) / 6))
        self.assertEqual(st["share"], {"relax": 33, "light": 33, "moderate": 17, "high": 17})
        sp = self.s["spo2"]
        self.assertEqual((sp["avg"], sp["min"], sp["max"], sp["min_raw"], sp["isolated_dips"]), (97, 97, 99, 94, 1))
        self.assertEqual(self.s["pai"]["week"], 89)
        self.assertEqual(self.s["pai"]["today"], 37)
        self.assertEqual(self.s["battery"]["v"], 77)

    def test_calories(self):
        k = self.s["kcal"]
        self.assertEqual(k["total"], k["bmr"] + k["active"])
        self.assertGreater(k["active"], 0)
        # plausible range: basal metabolism ±10% and active calories ±25%
        self.assertLess(k["range"][0], k["total"])
        self.assertGreater(k["range"][1], k["total"])
        # the walk at HR 120 is above 40% of heart rate reserve: each minute uses the higher of
        # the heart rate reserve method (Ferri Marini 2021) and the walking cost (Looney 2019)
        import helio_science as sci
        with closing(sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)) as con:
            p = self.h.profile(con, DAY)
        rest = self.s["hr"]["resting"]
        thr = rest + 0.4 * (p["hrmax"] - rest)
        self.assertGreater(118, thr)

        def per(hr, steps):
            return max(sci.hr_active_kcal(hr, rest, p["hrmax"], p["vo2max"], p["bmr"], p["weight"]),
                       sci.walk_kcal(steps, p["stride_m"], p["weight"]))

        walk = [x for x in self.s["workouts"] if T(x["name"]) == "Walking"][0]
        self.assertAlmostEqual(walk["kcal"], round(30 * per(120, 100) + 7 * per(118, 90)), delta=2)
        self.assertEqual(walk["kcal_range"], sci.kcal_range(walk["kcal"]))

    def test_not_worn_excluded(self):
        with closing(sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)) as con:
            day = self.h.compute_day(con, DAY, self.h.profile(con))
        self.assertTrue(all(r["hr"] is None for r in day["rows"] if r.get("RAW_KIND") == 115))

    def test_trends_and_health(self):
        self.assertEqual(len(self.t["days"]), 7)
        keys = [i["key"] for i in self.t["health"]["items"]]
        self.assertEqual(keys, ["steps", "activity", "strength", "rhr", "sleep", "efficiency", "waso", "latency"])
        strength = self.t["health"]["items"][2]
        self.assertEqual((strength["status"], T(strength["label"])), ("collecting", "3 days of data needed (1/3)"))
        for it in self.t["health"]["items"]:
            self.assertTrue(it["sources"], it["key"])

    def test_json_serializable(self):
        json.dumps(self.s)
        json.dumps(self.t)


class EdgeCases(unittest.TestCase):
    def test_empty_database(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "gb.db"
            con = sqlite3.connect(db)
            con.executescript(SCHEMA)
            con.commit()
            con.close()
            h = load_app(db)
            s = h.summary(DAY)
            self.assertEqual(s["steps"], 0)
            self.assertIsNone(s["sleep"])
            self.assertEqual(s["workouts"], [])
            self.assertIsNone(s["hr"]["min"])
            self.assertEqual(s["profile"]["missing"], ["age", "sex", "weight", "height"])
            json.dumps(h.trends(DAY))

    def test_minimal_database_without_optional_tables(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "gb.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE DEVICE (_id INTEGER PRIMARY KEY)")
            con.commit()
            con.close()
            h = load_app(db)
            json.dumps(h.summary(DAY))
            json.dumps(h.trends(DAY))

    def test_corrupt_sleep_blob_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "gb.db"
            build_db(db)
            con = sqlite3.connect(db)
            con.execute("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)", ((D0 + 8 * 3600) * 1000, b"\x00\x01"))
            con.commit()
            con.close()
            h = load_app(db)
            self.assertEqual(h.summary(DAY)["sleep"]["score"], 82)


class HttpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "gb.db"
        build_db(db)
        cls.h = load_app(db, HELIO_PASSWORD="segreta-123", HELIO_USER="helio")
        cls.srv = cls.h.ThreadingHTTPServer(("127.0.0.1", 0), cls.h.Handler)
        cls.lan = cls.h.ThreadingHTTPServer(("127.0.0.1", 0), cls.h.LanHandler)
        for s in (cls.srv, cls.lan):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.lan_base = f"http://127.0.0.1:{cls.lan.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for s in (cls.srv, cls.lan):
            s.shutdown()
            s.server_close()
        cls.tmp.cleanup()

    def get(self, url, headers=None):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_api_and_static(self):
        code, body, _ = self.get(f"{self.base}/api/summary?date={DAY.isoformat()}")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["date"], DAY.isoformat())
        code, body, _ = self.get(f"{self.base}/api/trends?end={DAY.isoformat()}")
        self.assertEqual(code, 200)
        for path in ("/", "/app.js", "/style.css", "/manifest.json", "/icon.svg", "/sw.js"):
            code, body, headers = self.get(self.base + path)
            self.assertEqual(code, 200, path)
            self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))

    def test_bad_input(self):
        code, body, _ = self.get(f"{self.base}/api/summary?date=2026-13-45")
        self.assertEqual(code, 200)  # invalid date: today is used
        self.assertEqual(self.get(f"{self.base}/../etc/passwd")[0], 404)
        self.assertEqual(self.get(f"{self.base}/helio_app.py")[0], 404)

    def test_lan_requires_password_then_remembers(self):
        self.assertEqual(self.get(f"{self.lan_base}/api/summary")[0], 401)
        bad = base64.b64encode(b"helio:sbagliata").decode()
        self.assertEqual(self.get(f"{self.lan_base}/", {"Authorization": f"Basic {bad}"})[0], 401)
        good = base64.b64encode(b"helio:segreta-123").decode()
        code, _, headers = self.get(f"{self.lan_base}/", {"Authorization": f"Basic {good}"})
        self.assertEqual(code, 200)
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(cookie.startswith("helio_device="))
        self.assertEqual(self.get(f"{self.lan_base}/api/summary", {"Cookie": cookie})[0], 200)
        self.assertEqual(self.get(f"{self.lan_base}/", {"Cookie": "helio_device=falso"})[0], 401)


@unittest.skipUnless(Path(LIVE_DB).is_file(), "real database not available")
class LiveDatabase(unittest.TestCase):
    """Internal consistency of your real data over the last 7 days."""

    @classmethod
    def setUpClass(cls):
        os.environ.pop("HELIO_NOW", None)
        cls.h = load_app(LIVE_DB)
        os.environ.pop("HELIO_NOW", None)
        cls.h = importlib.reload(cls.h)
        cls.today = cls.h.now().date()

    def test_days(self):
        for i in range(7):
            d = self.today - timedelta(days=i)
            s = self.h.summary(d)
            json.dumps(s)
            self.assertEqual(s["kcal"]["total"], s["kcal"]["bmr"] + s["kcal"]["active"], d)
            self.assertAlmostEqual(s["distance_km"], round(s["steps"] * s["goals"]["stride_m"] / 1000, 2), places=2)
            hr = s["hr"]
            if hr["min"] is not None:
                self.assertTrue(30 <= hr["min"] <= hr["avg"] <= hr["max"] <= 220, (d, hr))
            gb = [w for w in s["workouts"] if w["source"] == "gadgetbridge"]
            self.assertEqual(s["active_min"], round(sum(w["end"] - w["start"] for w in gb) / 60), d)
            for a, b in zip(gb, gb[1:]):
                self.assertLessEqual(a["end"], b["start"], d)
            sl = s["sleep"]
            if sl:
                self.assertEqual(sl["total"], sl["deep"] + sl["light"] + sl["rem"])
                self.assertTrue(0 < sl["efficiency"] <= 100)
                self.assertLessEqual(sl["start"], sl["end"])

    def test_band_summary_matches_stage_list(self):
        try:
            with closing(sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)) as con:
                rows = con.execute("SELECT DATA FROM HUAMI_SLEEP_SESSION_SAMPLE").fetchall()
        except sqlite3.Error:
            self.skipTest("no sleep summary")
        for (blob,) in rows:
            p = self.h.parse_sleep_blob(blob)
            if not p:
                continue
            counts = {"light": 0, "deep": 0, "rem": 0, "awake": 0}
            for a, b, s in p["stages"]:
                counts[s] += round((b - a) / 60)
            self.assertEqual(counts, p["totals"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
