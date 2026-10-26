"""Database in use by Gadgetbridge (HELIO_DB_LIVE=1, app on the phone).

On the server the database is a frozen copy opened with immutable=1. On the phone Gadgetbridge
writes while Helio reads: normal locks, reading the WAL and waiting (busy timeout) are needed.
"""
from contextlib import closing
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_helio import DAY, build_db, load_app  # noqa: E402

ENV_KEYS = ("HELIO_DB_LIVE", "HELIO_DB_TIMEOUT")


def wal_writer(db):
    """Connection writing in WAL mode without checkpoints: the data stays in the -wal file."""
    con = sqlite3.connect(db, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    return con


class LiveBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "gb.db"

    def tearDown(self):
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.pop("HELIO_STATE", None)
        self.tmp.cleanup()

    def app(self, live, timeout=None):
        env = {"HELIO_STATE": str(self.dir / "state")}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        if live:
            env["HELIO_DB_LIVE"] = "1"
        if timeout is not None:
            env["HELIO_DB_TIMEOUT"] = str(timeout)
        return load_app(self.db, **env)


class Config(LiveBase):
    def test_default_is_server_mode(self):
        h = self.app(live=False)
        self.assertFalse(h.DB_LIVE)
        self.assertEqual(h.DB_TIMEOUT, 5.0)

    def test_live_and_timeout_from_env(self):
        h = self.app(live=True, timeout=2.5)
        self.assertTrue(h.DB_LIVE)
        self.assertEqual(h.DB_TIMEOUT, 2.5)


class WalReads(LiveBase):
    def setUp(self):
        super().setUp()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE T (TIMESTAMP INTEGER, DEVICE_ID INTEGER, V INTEGER)")
            con.execute("INSERT INTO T VALUES (1, 1, 10)")
            con.commit()
        self.writer = wal_writer(self.db)
        self.writer.execute("INSERT INTO T VALUES (2, 1, 20)")  # stays in the WAL

    def tearDown(self):
        self.writer.close()
        super().tearDown()

    def rows(self, h):
        with closing(h.connect()) as con:
            return con.execute("SELECT V FROM T ORDER BY TIMESTAMP").fetchall()

    def test_live_reads_data_still_in_wal(self):
        self.assertTrue(Path(str(self.db) + "-wal").stat().st_size > 0)
        self.assertEqual(self.rows(self.app(live=True)), [(10,), (20,)])

    def test_server_mode_ignores_wal(self):
        # that is why immutable=1 cannot be used on the phone
        self.assertEqual(self.rows(self.app(live=False)), [(10,)])

    def test_live_connection_is_read_only(self):
        h = self.app(live=True)
        with closing(h.connect()) as con:
            with self.assertRaises(sqlite3.Error):
                con.execute("INSERT INTO T VALUES (3, 1, 30)")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM T").fetchone()[0], 2)

    def test_live_cache_key_follows_wal(self):
        h = self.app(live=True)
        before = h.db_mtime()
        main_before = os.stat(self.db).st_mtime_ns
        time.sleep(0.01)
        self.writer.execute("INSERT INTO T VALUES (3, 1, 30)")
        self.assertEqual(os.stat(self.db).st_mtime_ns, main_before)  # the main file does not change
        self.assertNotEqual(h.db_mtime(), before)

    def test_server_cache_key_ignores_wal(self):
        h = self.app(live=False)
        before = h.db_mtime()
        time.sleep(0.01)
        self.writer.execute("INSERT INTO T VALUES (3, 1, 30)")
        self.assertEqual(h.db_mtime(), before)

    def test_live_column_cache_sees_new_column(self):
        h = self.app(live=True)
        with closing(h.connect()) as con:
            self.assertNotIn("EXTRA", h.columns(con, "T"))
        time.sleep(0.01)
        self.writer.execute("ALTER TABLE T ADD COLUMN EXTRA INTEGER")
        with closing(h.connect()) as con:
            self.assertIn("EXTRA", h.columns(con, "T"))


class BusyTimeout(LiveBase):
    """Gadgetbridge writing with the classic journal blocks readers: Helio must wait."""

    def setUp(self):
        super().setUp()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("CREATE TABLE T (V INTEGER)")
            con.commit()

    def hold_lock(self, seconds):
        locked = threading.Event()

        def run():
            con = sqlite3.connect(self.db, isolation_level=None)
            con.execute("BEGIN EXCLUSIVE")
            con.execute("INSERT INTO T VALUES (1)")
            locked.set()
            time.sleep(seconds)
            con.execute("COMMIT")
            con.close()

        t = threading.Thread(target=run)
        t.start()
        locked.wait(5)
        return t

    def test_waits_for_writer(self):
        h = self.app(live=True, timeout=5)
        t = self.hold_lock(0.4)
        start = time.monotonic()
        with closing(h.connect()) as con:
            n = con.execute("SELECT COUNT(*) FROM T").fetchone()[0]
        waited = time.monotonic() - start
        t.join()
        self.assertEqual(n, 1)  # it waited for the write to finish
        self.assertGreaterEqual(waited, 0.2)

    def test_gives_up_after_timeout(self):
        h = self.app(live=True, timeout=0.1)
        t = self.hold_lock(1.0)
        try:
            with closing(h.connect()) as con:
                with self.assertRaises(sqlite3.OperationalError):
                    con.execute("SELECT COUNT(*) FROM T").fetchone()
        finally:
            t.join()


class SameResults(LiveBase):
    """With the live database the calculations give the same results as the frozen copy."""

    def test_summary_matches_server_mode(self):
        build_db(self.db)
        server = self.app(live=False).summary(DAY)
        live = self.app(live=True).summary(DAY)
        self.assertEqual(live, server)


if __name__ == "__main__":
    unittest.main()
