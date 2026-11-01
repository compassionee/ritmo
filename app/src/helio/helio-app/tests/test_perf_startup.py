"""Startup time regression test on a large synthetic database: six months of band data, one row a minute.

The page asks for these answers when it opens (app.js, load()); on the phone they run one after the other in a single
Python, so their sum is the wait before the first drawing. The test times a cold start (module just imported, empty
caches) and the same answers again (warm caches), and fails when the cold start gets much slower than the reference
measured on the development computer. The phone is about 3-5 times slower: the limits are for regressions, not goals.

Run it alone for the numbers:  python3 tests/test_perf_startup.py
"""
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from test_helio import SCHEMA, TZ, load_app, sleep_blob

DAYS = 183
END = date(2026, 3, 10)  # same "today" as the other tests (HELIO_NOW in load_app)
# the answers the page asks for at start, in its order (date = today)
STARTUP_PATHS = ("/api/settings", "/api/summary?date={d}", "/api/trends?end={d}", "/api/vitals?end={d}",
                 "/api/week?date={d}&which=auto", "/api/strength?date={d}", "/api/plan", "/api/weight",
                 "/api/nutrition?date={d}", "/api/foods", "/api/balance?date={d}", "/api/catalog/info")
# seconds; on the development computer (2026-11-01) the cold start takes about 0.35 s and the warm one 0.02 s. The
# limits leave room for a slower or busy machine: a regression of the kind this test is for (a cache lost, a scan of
# the whole table) costs several times more, and the counts below catch it exactly
COLD_LIMIT = float(os.environ.get("HELIO_PERF_COLD_LIMIT", "2.5"))
WARM_LIMIT = float(os.environ.get("HELIO_PERF_WARM_LIMIT", "0.5"))
# days computed from the minute rows at a cold start: the 28 days of the trends and today, never the whole history
MAX_DAYS_COMPUTED = 30


def day0(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp())


def build_large_db(path, days=DAYS, end=END, seed=7):
    """Gadgetbridge-like database with `days` days up to `end`: a minute row for every minute (sleep at night, walks,
    a run every third day, the band off one hour a day), the band's sleep summary, resting HR, HRV, SpO2, stress,
    breathing rate, temperature, PAI and battery."""
    rnd = random.Random(seed)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO DEVICE VALUES (1, 'Amazfit Helio Strap')")
    con.execute("INSERT INTO USER VALUES (1, 'test', ?, 1)", (int(datetime(1990, 4, 2, tzinfo=TZ).timestamp() * 1000),))
    con.execute("INSERT INTO USER_ATTRIBUTES VALUES (1, 178, 80, NULL, 8000, 0, NULL, 480, 1)")
    prev_bed = 23 * 60
    for i in range(days - 1, -1, -1):
        d = end - timedelta(days=i)
        t0 = day0(d)
        bed, wake = 23 * 60 + rnd.randrange(-40, 40), 7 * 60 + rnd.randrange(-30, 50)
        walk = rnd.randrange(8 * 60, 18 * 60)
        run = 18 * 60 + rnd.randrange(0, 60) if i % 3 == 0 else None
        off = rnd.randrange(12 * 60, 20 * 60)
        rows = []
        for m in range(24 * 60):
            kind, steps, hr, inten = 80, 0, 62 + rnd.randrange(-4, 5), rnd.randrange(0, 15)
            if m < wake or m >= bed:
                kind, hr, inten = 120, 52 + rnd.randrange(-3, 4), 0
            elif walk <= m < walk + 40:
                steps, hr, inten = 100 + rnd.randrange(-10, 10), 110 + rnd.randrange(-5, 6), 120
            elif run is not None and run <= m < run + 30:
                steps, hr, inten = 165, 155 + rnd.randrange(-6, 7), 200
            elif off <= m < off + 60:
                kind, hr = 115, 255
            elif rnd.random() < 0.15:
                steps, hr = rnd.randrange(5, 60), 75 + rnd.randrange(0, 15)
            rows.append((t0 + m * 60, 1, 1, inten, steps, kind, hr, 0, 0, 0, 0))
        con.executemany("INSERT INTO HUAMI_EXTENDED_ACTIVITY_SAMPLE VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        # the night that ends this morning, in minutes from midnight of the previous day
        s0, s1 = prev_bed, 1440 + wake
        prev_bed = bed
        stages, m, codes = [], s0, (4, 5, 4, 8, 4, 7)
        while m < s1 - 1:
            e = min(s1 - 1, m + rnd.randrange(20, 70))
            stages.append((m, e, codes[len(stages) % len(codes)]))
            m = e + 1
        con.execute("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)",
                    ((t0 + wake * 60) * 1000, sleep_blob(d, stages, score=60 + rnd.randrange(0, 30), written=t0 + wake * 60)))
        ms = (t0 + 8 * 3600) * 1000
        con.execute("INSERT INTO HUAMI_HEART_RATE_RESTING_SAMPLE VALUES (?,1,1,3600000,?)", (ms, 52 + rnd.randrange(-3, 4)))
        con.execute("INSERT INTO GENERIC_HRV_VALUE_SAMPLE VALUES (?,1,1,?)", ((t0 + 3 * 3600) * 1000, 45 + rnd.randrange(-8, 9)))
        con.execute("INSERT INTO GENERIC_TEMPERATURE_SAMPLE VALUES (?,1,1,?,2,9)", ((t0 + 3 * 3600) * 1000, 33.5 + rnd.random()))
        con.execute("INSERT INTO HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE VALUES (?,1,1,3600000,?)", ((t0 + 3 * 3600) * 1000, 14))
        con.execute("INSERT INTO HUAMI_PAI_SAMPLE VALUES (?,1,1,3600000,2,20,15,100,120,15,?,?)", (ms, 20.0, 80.0))
        con.executemany("INSERT INTO HUAMI_SPO2_SAMPLE VALUES (?,1,1,0,?)",
                        [((t0 + h * 3600) * 1000, 95 + rnd.randrange(0, 5)) for h in range(0, 7)])
        con.executemany("INSERT INTO HUAMI_STRESS_SAMPLE VALUES (?,1,1,1,?)",
                        [((t0 + h * 1800) * 1000, rnd.randrange(10, 80)) for h in range(16, 46)])
        con.execute("INSERT INTO BATTERY_LEVEL VALUES (?,1,?,0)", (t0 + 20 * 3600, 30 + i % 60))
    con.commit()
    con.close()


def seed_state(h, days=DAYS, end=END):
    """The user's side: two foods, a diary with three entries a day (complete), a weigh-in a week, a plan."""
    oats = h.save_food({"food": {"name": "Oats", "per100": {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7.0,
                                                            "fiber": 10.0, "sat": 1.3, "salt": 0.02}}})["id"]
    pasta = h.save_food({"food": {"name": "Pasta", "per100": {"kcal": 359, "protein": 12.5, "carbs": 71.2, "fat": 2.0,
                                                              "fiber": 3.0, "sat": 0.5, "salt": 0.01}}})["id"]
    for i in range(days - 1, 0, -1):
        d = (end - timedelta(days=i)).isoformat()
        h.diary_op({"date": d, "op": "add", "meal": "breakfast", "food": oats, "g": 80})
        h.diary_op({"date": d, "op": "add", "meal": "lunch", "food": pasta, "g": 150})
        h.diary_op({"date": d, "op": "add", "meal": "dinner", "food": pasta, "g": 120})
        h.diary_op({"date": d, "op": "complete", "complete": True})
        if i % 7 == 0:
            h.save_weight(end - timedelta(days=i), round(80 - (days - i) / 60, 1))


def request(h, path):
    """(status, body) of a GET through the real Handler, as the Android bridge runs it (no network)."""
    import io

    class Conn:
        def __init__(self, raw):
            self._in, self.sent = io.BytesIO(raw), bytearray()

        def makefile(self, mode, bufsize=-1):
            return self._in

        def sendall(self, data):
            self.sent += data

        def settimeout(self, t):
            pass

        def setsockopt(self, *a):
            pass

    class Srv:
        server_name, server_port = "localhost", 443

    c = Conn(f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
    h.Handler(c, ("127.0.0.1", 0), Srv())
    head, _, body = bytes(c.sent).partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), body


def get(h, path):
    status, body = request(h, path)
    return status, len(body)


def timed_start(h, d=END):
    """(path, seconds, status) of every startup answer, in the page's order."""
    out = []
    for p in STARTUP_PATHS:
        path = p.format(d=d.isoformat())
        t = time.perf_counter()
        status, _ = get(h, path)
        out.append((path.split("?")[0], time.perf_counter() - t, status))
    return out


class LargeDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.db = tmp / "gb.db"
        build_large_db(cls.db)
        cls.state = tmp / "state"
        h = load_app(cls.db, HELIO_STATE=str(cls.state))
        seed_state(h)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start(self):
        """A new process: the module imported again on the same data, caches empty."""
        t = time.perf_counter()
        h = load_app(self.db, HELIO_STATE=str(self.state))
        return h, time.perf_counter() - t

    def test_cold_and_warm_start(self):
        h, imp = self.start()
        computed = []
        orig = h._compute_day
        h._compute_day = lambda con, d, p: computed.append(d) or orig(con, d, p)
        try:
            cold = timed_start(h)
            n_cold = len(computed)
            warm = timed_start(h)
        finally:
            h._compute_day = orig
        self.assertLessEqual(n_cold, MAX_DAYS_COMPUTED, "days computed at a cold start")
        self.assertEqual(len(computed), n_cold, "warm caches: no day computed again")
        # the next start of the app: the past days come from the disk cache, only today is computed
        h, imp2 = self.start()
        computed.clear()
        orig = h._compute_day
        h._compute_day = lambda con, d, p: computed.append(d) or orig(con, d, p)
        try:
            disk = timed_start(h)
        finally:
            h._compute_day = orig
        self.assertEqual([d for d in computed if d < END], [], "past days at the next start")
        self.assertLess(imp2 + sum(x[1] for x in disk), COLD_LIMIT, "next start")
        for path, _, status in cold + warm:
            self.assertEqual(status, 200, path)
        total_cold, total_warm = sum(x[1] for x in cold), sum(x[1] for x in warm)
        report(imp, cold, warm, disk)
        self.assertLess(imp + total_cold, COLD_LIMIT, "cold start")
        self.assertLess(total_warm, WARM_LIMIT, "same answers with warm caches")

    def test_parallel_requests_compute_each_day_once(self):
        """The page sends its requests together: a day (activity, night, minute states) asked by several of them at
        once is computed by the first, the others wait for its result (Memo)."""
        import collections
        import threading
        shutil.rmtree(self.state / "cache", ignore_errors=True)
        h, _ = self.start()
        counts, lock = collections.Counter(), threading.Lock()
        for name in ("_compute_day", "_sleep_for", "_day_states"):
            orig = getattr(h, name)

            def counted(con, d, *a, _o=orig, _n=name):
                with lock:
                    counts[(_n, d)] += 1
                return _o(con, d, *a)
            setattr(h, name, counted)
        out = {}
        threads = [threading.Thread(target=lambda x=x: out.__setitem__(x, request(h, x.format(d=END))[0]))
                   for x in STARTUP_PATHS]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(set(out.values()), {200})
        self.assertGreaterEqual(len(counts), 27)
        self.assertEqual({k: v for k, v in counts.items() if v > 1}, {})

    def test_startup_timeline_records_the_first_answer_of_each_api(self):
        h, _ = self.start()
        timed_start(h)
        tl = h.startup_timeline()
        paths = [a["path"] for a in tl["api"]]
        self.assertEqual(paths[:3], ["/api/settings", "/api/summary", "/api/trends"])
        self.assertEqual(len(paths), len(set(paths)))                 # only the first answer of each
        self.assertTrue(all("?" not in p for p in paths))             # never the query (dates)
        for a in tl["api"]:
            self.assertLessEqual(a["start"], a["end"])
        self.assertIn("py.import", [m["key"] for m in tl["marks"]])


def report(imp, cold, warm, disk):
    if os.environ.get("HELIO_PERF_QUIET"):
        return
    print(f"\nimport helio_app: {imp * 1000:.0f} ms", file=sys.stderr)
    for (path, c, _), (_, w, _), (_, k, _) in zip(cold, warm, disk):
        print(f"  {path:<20} first {c * 1000:6.0f} ms   next start {k * 1000:6.0f} ms   warm {w * 1000:5.0f} ms",
              file=sys.stderr)
    tot = [sum(x[1] for x in r) * 1000 for r in (cold, disk, warm)]
    print(f"  {'total':<20} first {tot[0]:6.0f} ms   next start {tot[1]:6.0f} ms   warm {tot[2]:5.0f} ms", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
