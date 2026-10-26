"""Tests of the band's data format, checked against the parsers of the Zepp app 10.9.0
(static analysis of 12 October 2026): sleep summary 0x48 and stress bands.
"""
import sqlite3
import struct
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_helio import T, D0, DAY, build_db, hm, load_app, sleep_blob  # noqa: E402

STAGES = [(1500, 1529, 4), (1530, 1589, 5), (1590, 1649, 4), (1650, 1679, 8), (1680, 1689, 7),
          (1690, 1799, 4), (1800, 1859, 8)]


def with_naps(blob, naps, short_stages=0, supported=1, has=1):
    """Adds naps in the fields of the Zepp format: 0x13/0x14 flags, 0x17 count, 0x18 slots of 6 bytes."""
    b = bytearray(blob)
    b[0x13], b[0x14] = supported, has
    struct.pack_into("<b", b, 0x17, len(naps))
    for k, (s, e, length) in enumerate(naps):
        struct.pack_into("<3h", b, 0x18 + 6 * k, s, e, length)
    b[0x55] = short_stages
    return bytes(b)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "gb.db"
        build_db(cls.db)
        cls.h = load_app(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class SleepBlobFormat(Base):
    def test_main_stage_count_is_a_single_byte(self):
        # with naps byte 0x55 (nap stages) is not zero: it used to be read together
        # with the number of main stages and the night's summary was discarded
        blob = with_naps(sleep_blob(DAY, STAGES), [], short_stages=4, has=0)
        p = self.h.parse_sleep_blob(blob)
        self.assertIsNotNone(p)
        self.assertEqual(len(p["stages"]), len(STAGES))
        self.assertEqual(p["totals"], {"rem": 90, "light": 200, "deep": 60, "awake": 10})

    def test_naps_are_read(self):
        blob = with_naps(sleep_blob(DAY, STAGES), [(2220, 2239, 20), (2400, 2429, 30)], short_stages=3)
        p = self.h.parse_sleep_blob(blob)
        self.assertEqual(len(p["naps"]), 2)
        first = p["naps"][0]
        # 2220 minutes from midnight of the previous day = 13:00 of the day
        self.assertEqual((first["start"], first["end"], first["minutes"]), (hm(13, 0), hm(13, 20), 20))
        self.assertEqual(p["naps"][1]["minutes"], 30)
        self.assertEqual(len(p["stages"]), len(STAGES))  # naps are not part of the night's stages

    def test_naps_ignored_without_flags(self):
        blob = with_naps(sleep_blob(DAY, STAGES), [(2220, 2239, 20)], has=0)
        self.assertEqual(self.h.parse_sleep_blob(blob)["naps"], [])
        blob = with_naps(sleep_blob(DAY, STAGES), [(2220, 2239, 20)], supported=0)
        self.assertEqual(self.h.parse_sleep_blob(blob)["naps"], [])

    def test_invalid_nap_slots_are_skipped(self):
        blob = with_naps(sleep_blob(DAY, STAGES), [(2220, 2239, 20), (0, 0, 0), (2500, 2400, 15)])
        naps = self.h.parse_sleep_blob(blob)["naps"]
        self.assertEqual([n["minutes"] for n in naps], [20])

    def test_nap_count_is_capped_at_ten_slots(self):
        b = bytearray(with_naps(sleep_blob(DAY, STAGES), [(2220 + 30 * k, 2229 + 30 * k, 10) for k in range(10)]))
        struct.pack_into("<b", b, 0x17, 99)
        self.assertEqual(len(self.h.parse_sleep_blob(bytes(b))["naps"]), 10)

    def test_no_main_sleep_flag(self):
        b = bytearray(sleep_blob(DAY, STAGES))
        b[9] = 0
        self.assertIsNone(self.h.parse_sleep_blob(bytes(b)))

    def test_version_zero_moves_end_one_minute(self):
        b = bytearray(sleep_blob(DAY, STAGES))
        b[8] = 1
        end_v1 = self.h.parse_sleep_blob(bytes(b))["end"]
        b[8] = 0
        self.assertEqual(self.h.parse_sleep_blob(bytes(b))["end"], end_v1 + 60)

    def test_stage_count_out_of_range(self):
        for value in (0, 51, 0xFF):  # 0xFF = -1 as s8
            b = bytearray(sleep_blob(DAY, STAGES))
            b[0x54] = value
            self.assertIsNone(self.h.parse_sleep_blob(bytes(b)), value)

    def test_negative_stage_minutes_rejected(self):
        b = bytearray(sleep_blob(DAY, STAGES))
        struct.pack_into("<h", b, 0x56, -5)
        self.assertIsNone(self.h.parse_sleep_blob(bytes(b)))

    def test_short_blob_rejected(self):
        self.assertIsNone(self.h.parse_sleep_blob(sleep_blob(DAY, STAGES)[:593]))


class SleepSummaryWithNaps(unittest.TestCase):
    def test_summary_shows_naps_apart_from_night(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "gb.db"
            build_db(db)
            blob = with_naps(sleep_blob(DAY, STAGES, bed=(1480, 1865), score=82, written=D0 + 15 * 3600),
                             [(2220, 2239, 20)], short_stages=2)
            con = sqlite3.connect(db)
            con.execute("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)", ((D0 + 15 * 3600) * 1000, blob))
            con.commit()
            con.close()
            sl = load_app(db).summary(DAY)["sleep"]
            self.assertEqual(sl["source"], "band")
            self.assertEqual((sl["nap_min"], len(sl["naps"])), (20, 1))
            self.assertEqual(sl["total"], 60 + 90 + 200)  # the night's duration does not change
            self.assertEqual(sl["efficiency"], round(100 * 350 / 385))

    def test_fallback_from_samples_has_no_naps(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "gb.db"
            build_db(db)
            con = sqlite3.connect(db)
            con.execute("DELETE FROM HUAMI_SLEEP_SESSION_SAMPLE")
            con.commit()
            con.close()
            sl = load_app(db).summary(DAY)["sleep"]
            self.assertEqual(sl["source"], "samples")
            self.assertEqual((sl["naps"], sl["nap_min"]), ([], 0))


class StressBands(Base):
    def test_band_limits_like_zepp(self):
        vals = [(i, v) for i, v in enumerate([0, 39, 40, 59, 60, 79, 80, 100])]
        self.assertEqual(self.h.stress_summary(vals)["share"],
                         {"relax": 25, "light": 25, "moderate": 25, "high": 25})

    def test_zero_kept_and_values_over_100_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "gb.db"
            build_db(db)
            con = sqlite3.connect(db)
            for i, v in enumerate([0, 150]):
                con.execute("INSERT INTO HUAMI_STRESS_SAMPLE VALUES (?,1,1,1,?)", ((D0 + 13 * 3600 + i * 60) * 1000, v))
            con.commit()
            con.close()
            st = load_app(db).summary(DAY)["stress"]
            vals = [20, 30, 45, 50, 65, 85, 0]
            self.assertEqual(st["avg"], round(sum(vals) / len(vals)))
            self.assertEqual(st["share"], {"relax": 43, "light": 29, "moderate": 14, "high": 14})

    def test_frontend_shows_naps(self):
        app = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
        html = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()
        for i in ("sleepNapWrap", "sleepNaps"):
            self.assertIn(f'id="{i}"', html)
            self.assertIn(f'$("{i}")', app)


if __name__ == "__main__":
    unittest.main(verbosity=2)
