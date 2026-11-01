"""Disk cache of past days (helio_app: disk_load, disk_save): the next start reads the past days instead of computing
them again, and the answers are the same as without the cache. Every case that must compute a day again is tested:
data of an old day synced late, a label, the profile, the plan, a new version of the app, a damaged file. The cache
stays out of the export and is dropped at an import.
"""
import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from test_helio import load_app
from test_perf_startup import END, STARTUP_PATHS, build_large_db, request, seed_state

import helio_transfer as ht  # noqa: E402 (after test_helio, which puts the app on the path)

DAYS = 35  # the 28 days of the trends, and a little more
PAST = END - timedelta(days=3)
WINDOW = 27  # past days of the 28-day trends: all computed again when an input of every day changes
PLAN = json.loads((Path(__file__).resolve().parent / "plan-sample.json").read_text())


class DayCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.db0 = cls.base / "gb0.db"
        build_large_db(cls.db0, days=DAYS)
        cls.state0 = cls.base / "state0"
        seed_state(load_app(cls.db0, HELIO_STATE=str(cls.state0), HELIO_APP_VERSION="270-1"), days=DAYS)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        # every test on its own copy: database, data and an empty cache
        self.dir = Path(tempfile.mkdtemp(dir=self.base))
        self.db, self.state = self.dir / "gb.db", self.dir / "state"
        shutil.copy(self.db0, self.db)
        shutil.copytree(self.state0, self.state)
        shutil.rmtree(self.state / "cache", ignore_errors=True)

    def start(self, version="270-1"):
        """A new start of the app on the same files; counts the past days computed from the rows."""
        h = load_app(self.db, HELIO_STATE=str(self.state), HELIO_APP_VERSION=version)
        self.computed = []
        for name in ("_compute_day", "_sleep_for", "_day_states"):
            orig = getattr(h, name)
            setattr(h, name, (lambda o, n: lambda con, d, *a: self.computed.append((n, d)) or o(con, d, *a))(orig, name))
        return h

    def answers(self, h, extra=()):
        out = {}
        for p in STARTUP_PATHS + tuple(extra):
            path = p.format(d=END.isoformat())
            out[path] = get_body(h, path)
        return out

    def past(self, kind=None):
        return sorted({d for n, d in self.computed if d < END and (kind is None or n == kind)})

    def test_second_start_reads_the_past_days_with_the_same_answers(self):
        h = self.start()
        first = self.answers(h, extra=(f"/api/summary?date={PAST}",))
        self.assertGreaterEqual(len(self.past("_compute_day")), WINDOW)
        files = sorted(p.name for p in (self.state / "cache").iterdir())
        self.assertIn(f"day-{PAST}.json", files)
        self.assertNotIn(f"day-{END}.json", files)                    # today is never saved: it still changes
        h = self.start()
        again = self.answers(h, extra=(f"/api/summary?date={PAST}",))
        self.assertEqual(again, first)
        # only today, and the day shown with its minute rows (not on disk)
        self.assertEqual(self.past("_compute_day"), [PAST])
        self.assertEqual(self.past("_sleep_for"), [])
        self.assertEqual(self.past("_day_states"), [])

    def test_old_data_synced_late_computes_that_day_again(self):
        self.answers(self.start())
        day = END - timedelta(days=5)
        t = int(h_midnight(self.start(), day)) + 12 * 3600
        import sqlite3
        with sqlite3.connect(self.db) as con:  # the band sends minutes of that day only now
            con.execute('UPDATE HUAMI_EXTENDED_ACTIVITY_SAMPLE SET STEPS=STEPS+500 WHERE "TIMESTAMP" BETWEEN ? AND ?',
                        (t, t + 1800))
        h = self.start()
        got = self.answers(h)
        self.assertEqual(self.past("_compute_day"), [day])
        shutil.rmtree(self.state / "cache")
        self.assertEqual(got, self.answers(self.start()))           # same as computing everything again

    def test_label_changed(self):
        h = self.start()
        self.answers(h)
        trainings = [t for x in h.history(h.connect(), PAST, 1) for t in x["trainings"]]
        self.assertTrue(trainings)
        t = trainings[0]
        h.save_label(t["start"], t["end"], "strength", 1, None)
        h = self.start()
        got = self.answers(h)
        self.assertGreaterEqual(len(self.past("_compute_day")), WINDOW)   # labels teach the other days too (similar ones)
        self.assertEqual(self.past("_sleep_for"), [])                 # the nights do not depend on labels
        shutil.rmtree(self.state / "cache")
        self.assertEqual(got, self.answers(self.start()))

    def test_profile_changed(self):
        self.answers(self.start())
        self.start().save_profile({"profile": {"hrmax": 175}})
        got = self.answers(self.start())
        self.assertGreaterEqual(len(self.past("_compute_day")), WINDOW)
        shutil.rmtree(self.state / "cache")
        self.assertEqual(got, self.answers(self.start()))

    def test_plan_changed(self):
        self.answers(self.start())
        self.start().save_plan({"plan": PLAN})
        self.answers(self.start())
        self.assertGreaterEqual(len(self.past("_compute_day")), WINDOW)

    def test_new_version_of_the_app(self):
        self.answers(self.start("270-1"))
        self.answers(self.start("271-2"))
        self.assertGreaterEqual(len(self.past("_compute_day")), WINDOW)
        self.assertGreaterEqual(len(self.past("_sleep_for")), WINDOW)
        self.answers(self.start("271-2"))
        self.assertEqual(self.past(), [])

    def test_damaged_file_is_computed_again_and_replaced(self):
        first = self.answers(self.start())
        bad = {"day": "{not json", "sleep": json.dumps({"key": "x"}), "states": json.dumps([1, 2])}
        for kind, content in bad.items():
            (self.state / "cache" / f"{kind}-{PAST}.json").write_text(content)
        got = self.answers(self.start())
        self.assertEqual(got, first)
        self.assertEqual(sorted(set(self.computed) - {(n, d) for n, d in self.computed if d >= END}),
                         [("_compute_day", PAST), ("_day_states", PAST), ("_sleep_for", PAST)])
        for kind in bad:
            self.assertIn("key", json.loads((self.state / "cache" / f"{kind}-{PAST}.json").read_text()))

    def test_cache_that_cannot_be_written_is_only_slower(self):
        (self.state / "cache").write_text("a file where the folder should be")
        first = self.answers(self.start())
        self.assertEqual(first, self.answers(self.start()))
        self.assertGreaterEqual(len(self.past("_compute_day")), WINDOW)

    def test_days_older_than_400_are_pruned_at_start(self):
        self.answers(self.start())
        cache = self.state / "cache"
        old = [f"day-{END - timedelta(days=401)}.json", f"sleep-{END - timedelta(days=900)}.json",
               f"states-{END - timedelta(days=401)}.json", f".day-{PAST}.json.123.tmp", "day-2026-13-45.json"]
        kept = [f"day-{END - timedelta(days=400)}.json", "notes.txt"]
        for name in old + kept:
            (cache / name).write_text("{}")
        recent = sorted(p.name for p in cache.iterdir() if p.name not in old + kept)
        self.start()                                                   # the start prunes
        left = sorted(p.name for p in cache.iterdir())
        self.assertEqual(left, sorted(recent + kept))
        h = self.start()
        self.assertIn("py.cache_prune", [m["key"] for m in h.startup_timeline()["marks"]])

    def test_out_of_the_export_and_dropped_at_an_import(self):
        self.answers(self.start())
        self.assertTrue(any((self.state / "cache").iterdir()))
        data = ht.export_bytes(self.state)
        self.assertNotIn(b"cache", data.replace(b"helio-cache", b""))  # no file of the cache in the archive
        _, files = ht.read_archive(data)
        self.assertFalse([n for n in files if "cache" in n or n.startswith(("day-", "sleep-", "states-"))])
        _, _, backup = ht.import_bytes(data, self.state)
        self.assertFalse((self.state / "cache").exists())
        self.assertFalse((Path(backup) / "cache").exists())         # not even in the backup of the old data
        self.answers(self.start())                                    # computed again from the imported data
        self.assertGreaterEqual(len(self.past("_compute_day")), WINDOW)


def get_body(h, path):
    status, body = request(h, path)
    assert status == 200, (path, status, body[:200])
    return body.decode()


def h_midnight(h, d):
    return h.midnight(d)


if __name__ == "__main__":
    unittest.main()
