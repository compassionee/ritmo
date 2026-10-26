"""Caches and speed: what is computed once, what is computed again and what is never touched.

- The page sends 9 requests together: each day is computed once, the others wait for it.
- Today is computed once a minute; a past day again only when its own rows change, also after Gadgetbridge wrote
  other rows (a sync brings today's data; a battery level touches no day), and then with the new data.
- With no write, nothing is computed and no row is read again (the cache keyed on the database's modification).
- One snapshot per request: every query of a request sees the same data.
- The food catalog: opened once per file, read only, closed when replaced or removed, never touched by the
  requests of Today, Fitness, Sleep and Health.
"""
import collections
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from test_dst import build, local
from test_helio import load_app

FIRST, LAST = date(2026, 10, 1), date(2026, 10, 31)
TODAY = LAST
PAST = date(2026, 10, 20)
PAGE = ["/api/summary?date={d}", "/api/trends?end={d}", "/api/vitals?end={d}", "/api/week?date={d}&which=auto",
        "/api/strength?date={d}", "/api/plan", "/api/weight", "/api/nutrition?date={d}", "/api/foods"]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.db = cls.dir / "gb.db"
        build(cls.db, [(FIRST, LAST)])
        # Gadgetbridge keeps its database open in WAL mode while it runs
        cls.writer = sqlite3.connect(cls.db)
        cls.writer.execute("PRAGMA journal_mode=WAL")
        cls.writer.execute("INSERT INTO BATTERY_LEVEL VALUES (?, 1, 90, 0)", (local(FIRST, 0),))  # the WAL file exists
        cls.writer.commit()
        cls.h = load_app(cls.db, HELIO_STATE=str(cls.dir / "state"), HELIO_DB_LIVE="1",
                         HELIO_NOW=str(local(TODAY, 21)))

    @classmethod
    def tearDownClass(cls):
        cls.writer.close()
        for k in ("HELIO_STATE", "HELIO_DB_LIVE"):
            os.environ.pop(k, None)
        cls.tmp.cleanup()

    def setUp(self):
        self.h.clear_caches()

    def get(self, path):
        """GET through the same Handler as the server and the app, in this thread."""
        class Conn:
            def __init__(self, raw):
                import io
                self.rin, self.sent = io.BytesIO(raw), bytearray()

            def makefile(self, mode, bufsize=-1):
                return self.rin

            def sendall(self, data):
                self.sent += data

            def settimeout(self, timeout):
                pass

            def setsockopt(self, *args):
                pass

        raw = f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode()
        c = Conn(raw)
        self.h.Handler(c, ("127.0.0.1", 0), type("S", (), {"server_name": "x", "server_port": 80})())
        head, _, body = bytes(c.sent).partition(b"\r\n\r\n")
        self.assertIn(b" 200 ", head.split(b"\r\n")[0], (path, body[:200]))
        return body

    def page(self, d=TODAY):
        """The 9 requests of the page, together, as the WebView sends them."""
        threads = [threading.Thread(target=self.get, args=(p.format(d=d.isoformat()),)) for p in PAGE]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def count(self, name):
        """Counts the calls of a function of helio_app (by the day or the arguments it gets)."""
        real = getattr(self.h, name)
        calls = collections.Counter()

        def wrapper(*a, **kw):
            calls[next((x.isoformat() for x in a if isinstance(x, date)), "-")] += 1
            return real(*a, **kw)
        patcher = mock.patch.object(self.h, name, wrapper)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def write(self, sql, args=()):
        self.writer.execute(sql, args)
        self.writer.commit()


class SharedWork(Base):
    def test_the_page_computes_each_day_once(self):
        days, nights = self.count("_compute_day"), self.count("_sleep_for")
        self.page()
        self.assertTrue(days and nights)
        self.assertEqual(max(days.values()), 1, days)        # 9 requests together, each day once
        self.assertEqual(max(nights.values()), 1, nights)

    def test_nothing_is_computed_again_without_a_write(self):
        self.page()
        days, nights, sigs = self.count("_compute_day"), self.count("_sleep_for"), self.count("table_sig")
        self.page()
        self.assertEqual((sum(days.values()), sum(nights.values()), sum(sigs.values())), (0, 0, 0))

    def test_today_once_a_minute(self):
        self.get(f"/api/summary?date={TODAY}")
        days = self.count("_compute_day")
        self.get(f"/api/summary?date={TODAY}")
        self.assertEqual(days[TODAY.isoformat()], 0)
        with mock.patch.dict(os.environ, {"HELIO_NOW": str(local(TODAY, 21, 1))}):
            self.get(f"/api/summary?date={TODAY}")
        self.assertEqual(days[TODAY.isoformat()], 1)
        self.assertEqual(sum(v for k, v in days.items() if k != TODAY.isoformat()), 0)   # the past stays


class AfterAWrite(Base):
    def test_a_write_elsewhere_keeps_the_past_days(self):
        self.page()
        days = self.count("_compute_day")
        # Gadgetbridge writes the battery level and today's last minutes (a sync)
        self.write("INSERT INTO BATTERY_LEVEL VALUES (?, 1, 55, 0)", (local(TODAY, 21),))
        self.write("UPDATE HUAMI_EXTENDED_ACTIVITY_SAMPLE SET STEPS=STEPS+30 WHERE TIMESTAMP=?", (local(TODAY, 20, 30),))
        self.page()
        self.assertEqual({k for k, v in days.items() if v}, {TODAY.isoformat()})
        s = json.loads(self.get(f"/api/summary?date={TODAY}"))
        self.assertEqual(sum(s["steps_by_hour"]), s["steps"])

    def test_a_past_day_whose_rows_change_is_computed_again(self):
        before = json.loads(self.get(f"/api/summary?date={PAST}"))
        days = self.count("_compute_day")
        # a late sync brings 500 steps at 10:00 of that day
        self.write("UPDATE HUAMI_EXTENDED_ACTIVITY_SAMPLE SET STEPS=500 WHERE TIMESTAMP=?", (local(PAST, 10),))
        after = json.loads(self.get(f"/api/summary?date={PAST}"))
        self.assertEqual(days[PAST.isoformat()], 1)
        self.assertEqual(after["steps"], before["steps"] - 2 + 500)
        self.assertEqual(days[(PAST - timedelta(days=5)).isoformat()], 0)      # the other days stay

    def test_a_new_band_summary_of_a_past_night(self):
        with closing(self.h.connect()) as con:
            old = self.h.sleep_for(con, PAST)
        from test_dst import night_blob
        late = local(PAST, 22)
        self.write("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)",
                   (late * 1000, night_blob(PAST, local(PAST - timedelta(days=1), 22), local(PAST, 7), score=88,
                                            written=late)))
        with closing(self.h.connect()) as con:
            new = self.h.sleep_for(con, PAST)
        self.assertEqual((old["total"], new["total"]), (480, 540))
        self.assertEqual(new["score"], 88)

    def test_one_snapshot_per_request(self):
        with closing(self.h.connect()) as con:
            con.execute("SELECT COUNT(*) FROM BATTERY_LEVEL").fetchone()
            self.assertTrue(con.in_transaction)
            n = con.execute("SELECT COUNT(*) FROM BATTERY_LEVEL").fetchone()[0]
            self.write("INSERT INTO BATTERY_LEVEL VALUES (?, 1, 40, 0)", (local(TODAY, 22),))
            # Gadgetbridge wrote meanwhile: this request keeps seeing the data it started with
            self.assertEqual(con.execute("SELECT COUNT(*) FROM BATTERY_LEVEL").fetchone()[0], n)
        with closing(self.h.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM BATTERY_LEVEL").fetchone()[0], n + 1)


class MemoTests(unittest.TestCase):
    def setUp(self):
        import helio_app
        self.Memo = helio_app.Memo

    def test_one_computation_for_requests_together(self):
        m, calls, out = self.Memo(), [], []

        def slow():
            calls.append(1)
            time.sleep(0.2)
            return "v"
        threads = [threading.Thread(target=lambda: out.append(m.get("k", slow))) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual((len(calls), out), (1, ["v"] * 5))

    def test_a_failure_is_not_kept(self):
        m = self.Memo()
        with self.assertRaises(ZeroDivisionError):
            m.get("k", lambda: 1 / 0)
        self.assertEqual(m.get("k", lambda: 2), 2)

    def test_versions(self):
        m = self.Memo()
        self.assertEqual(m.get("k", lambda: 1, version=1), 1)
        self.assertEqual(m.get("k", lambda: 2, version=1), 1)                      # same version: kept
        self.assertEqual(m.get("k", lambda: 3, version=2, still_valid=lambda v: True), 1)  # rows unchanged
        self.assertEqual(m.get("k", lambda: 4, version=3, still_valid=lambda v: False), 4)

    def test_size_limit_drops_the_oldest(self):
        m = self.Memo(maxsize=4)
        for k in range(6):
            m.get(k, lambda k=k: k)
        self.assertLessEqual(len(m), 4)
        self.assertEqual(m.get(5, lambda: "again"), 5)


class FoodCatalogUse(Base):
    """The catalog is opened once, read only, and only by the food search."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import helio_catalog as hc
        cls.hc = hc
        bundled = Path(cls.h.__file__).resolve().parent / "catalog" / "generic.tar"
        cls.cat = cls.dir / "catalog"
        hc.install(bundled, cls.cat, cls.dir / "state", kinds=("generic",))
        cls.h.CATALOG_DIR = cls.cat

    def setUp(self):
        super().setUp()
        self.hc.close_all()
        self.h._catalog_checked = False

    def opened(self):
        """Paths of the catalog files opened from now on."""
        real, paths = sqlite3.connect, []

        def spy(target, *a, **kw):
            if str(self.cat) in str(target):
                paths.append(str(target))
            return real(target, *a, **kw)
        patcher = mock.patch.object(self.hc.sqlite3, "connect", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return paths

    def test_opened_once_read_only(self):
        paths = self.opened()
        for q in ("pollo", "riso", "mela", "pasta", "latte"):
            self.assertTrue(self.hc.search(self.cat, q, "it"))
        self.hc.by_barcode(self.cat, "8001234567890")
        self.assertEqual(len(paths), 1, paths)
        self.assertIn("mode=ro&immutable=1", paths[0])
        with self.hc._reading(self.cat, "generic") as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("CREATE TABLE x (a)")

    def test_a_new_file_gets_a_new_connection_and_the_old_one_is_closed(self):
        self.hc.search(self.cat, "pollo", "it")
        with self.hc._reading(self.cat, "generic") as con:
            old = con
        bundled = Path(self.h.__file__).resolve().parent / "catalog" / "generic.tar"
        self.hc.install(bundled, self.cat, self.dir / "state", kinds=("generic",))
        with self.assertRaises(sqlite3.ProgrammingError):
            old.execute("SELECT 1")                               # closed: the replaced file is released
        self.assertTrue(self.hc.search(self.cat, "pollo", "it"))

    def test_removed_file_is_not_kept_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "catalog"
            bundled = Path(self.h.__file__).resolve().parent / "catalog" / "generic.tar"
            self.hc.install(bundled, cat, Path(tmp) / "state", kinds=("generic",))
            # a copy installed as the community catalog, to remove it
            os.replace(cat / "generic.sqlite", cat / "off.sqlite")
            m = json.loads((cat / "generic.json").read_text())
            (cat / "off.json").write_text(json.dumps({**m, "kind": "off"}))
            with self.hc._reading(cat, "off") as con:
                self.assertIsNotNone(con)
            self.hc.remove("off", cat, Path(tmp) / "state")
            if Path("/proc/self/fd").is_dir():
                open_files = [os.readlink(f"/proc/self/fd/{fd}") for fd in os.listdir("/proc/self/fd")
                              if os.path.exists(f"/proc/self/fd/{fd}")]
                self.assertFalse([f for f in open_files if "off.sqlite" in f], open_files)

    def test_today_fitness_sleep_and_health_never_touch_it(self):
        reading, ready = mock.MagicMock(side_effect=self.hc._reading), mock.MagicMock()
        real = sqlite3.connect

        def guard(target, *a, **kw):
            if str(self.cat) in str(target):
                raise AssertionError(f"catalog opened: {target}")
            return real(target, *a, **kw)
        with mock.patch.object(self.hc, "_reading", reading), mock.patch.object(self.hc, "ensure_bundled", ready), \
                mock.patch.object(self.hc.sqlite3, "connect", guard):
            self.page()
            self.page(PAST)
            for p in ("/api/settings", "/api/profile", f"/api/export.csv?kind=days&from={PAST}&to={TODAY}"):
                self.get(p)
        reading.assert_not_called()
        ready.assert_not_called()
        self.assertFalse(self.h._catalog_checked)

    def test_the_search_uses_it(self):
        reading = mock.MagicMock(side_effect=self.hc._reading)
        with mock.patch.object(self.hc, "_reading", reading):
            self.get("/api/catalog/search?q=pollo&lang=it")
        self.assertTrue(reading.called)


if __name__ == "__main__":
    unittest.main()
