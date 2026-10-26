"""Time zone and clock changes.

Every timestamp is converted with the offset of its own date (zoneinfo), never with today's or a fixed one, so
the history does not move when the clock changes. On the day the clock goes back there are 25 hours and one hour
repeats; on the day it goes forward there are 23 and one hour is missing.

Dates (Europe/Rome): 25 October 2026, from +2 to +1 at 03:00 (02:00-02:59 twice); 29 March 2026 and 28 March 2027,
from +1 to +2 at 02:00 (02:00-02:59 missing).
"""
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from test_helio import SCHEMA, TZ, build_db, load_app, sleep_blob

APP = Path(__file__).resolve().parent.parent
AUTUMN = date(2026, 10, 25)
SPRING = date(2027, 3, 28)
SPRING_2026 = date(2026, 3, 29)
ONE = timedelta(days=1)


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp())


def local(d, h=0, m=0, fold=0):
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=TZ, fold=fold).timestamp())


def band_minutes(day, t):
    """Minute written by the band for instant t in the summary of day: counted from 24 hours before its midnight."""
    return (t - (local(day) - 86400)) // 60


def night_blob(day, start, end, score=70, written=None):
    """Band summary of the night ending on day, asleep (light) from instant start to instant end."""
    return sleep_blob(day, [(band_minutes(day, start), band_minutes(day, end) - 1, 4)], score=score,
                      written=written)


def build(path, ranges, late_summary=None, no_summary=(), extra=()):
    """Band worn every minute of the days in ranges: asleep from 23:00 to 07:00 local time, 70 bpm and a few steps
    while awake, 50 bpm asleep; the band's sleep summary of every night (except the days in no_summary); a resting
    heart rate every morning. extra: (instant, steps, kind) rows replacing the generated minute."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO DEVICE VALUES (1, 'Amazfit Helio Strap')")
    con.execute("INSERT INTO USER VALUES (1, 'test', ?, 1)", (int(datetime(1992, 5, 20, tzinfo=TZ).timestamp() * 1000),))
    con.execute("INSERT INTO USER_ATTRIBUTES VALUES (1, 171, 74, NULL, 8000, 0, NULL, 480, 1)")
    extra = {t: (steps, kind) for t, steps, kind in extra}
    rows = {}
    for first, last in ranges:
        d = first
        while d <= last:
            asleep = [(local(d - ONE, 23), local(d, 7)), (local(d, 23), local(d + ONE, 7))]
            for t in range(local(d), local(d + ONE), 60):
                sleeping = any(a <= t < b for a, b in asleep)
                steps, kind = extra.get(t, (0 if sleeping else 2, 120 if sleeping else 80))
                rows[t] = (t, 1, 1, 5, steps, kind, 50 if sleeping else 70, 0, 0, 0, 0)
            if d != first and d not in no_summary:
                con.execute("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)",
                            ((local(d, 9)) * 1000, night_blob(d, local(d - ONE, 23), local(d, 7))))
            con.execute("INSERT INTO HUAMI_HEART_RATE_RESTING_SAMPLE VALUES (?,1,1,0,52)", (local(d, 8) * 1000,))
            for i in range(0, 24 * 12):
                t = local(d) + i * 300
                if t < local(d + ONE):
                    con.execute("INSERT INTO HUAMI_STRESS_SAMPLE VALUES (?,1,1,1,30)", (t * 1000,))
            d += ONE
    con.executemany("INSERT INTO HUAMI_EXTENDED_ACTIVITY_SAMPLE VALUES (?,?,?,?,?,?,?,?,?,?,?)", sorted(rows.values()))
    if late_summary:
        con.execute("INSERT INTO HUAMI_SLEEP_SESSION_SAMPLE VALUES (?,1,1,?)", late_summary)
    con.commit()
    con.close()


class Conversions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build_db(Path(cls.tmp.name) / "gb.db")
        # "now" is winter: the summer instants must still read with summer time
        cls.h = load_app(Path(cls.tmp.name) / "gb.db", HELIO_STATE=str(Path(cls.tmp.name) / "state"),
                         HELIO_NOW=str(utc(2026, 11, 20, 12)))

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def test_day_length(self):
        h = self.h
        self.assertEqual(h.midnight(AUTUMN), utc(2026, 10, 24, 22))
        self.assertEqual(h.midnight(AUTUMN + ONE), utc(2026, 10, 25, 23))
        self.assertEqual(h.midnight(AUTUMN + ONE) - h.midnight(AUTUMN), 25 * 3600)
        for d in (SPRING, SPRING_2026):
            self.assertEqual(h.midnight(d + ONE) - h.midnight(d), 23 * 3600)
        for d in (AUTUMN - ONE, AUTUMN + ONE, SPRING - ONE, SPRING + ONE, date(2026, 7, 1), date(2026, 12, 1)):
            self.assertEqual(h.midnight(d + ONE) - h.midnight(d), 24 * 3600, d)

    def test_each_date_with_its_own_offset(self):
        h = self.h
        self.assertEqual(h.at(AUTUMN - ONE, 10), utc(2026, 10, 24, 8))     # summer time, +2
        self.assertEqual(h.at(AUTUMN, 10), utc(2026, 10, 25, 9))           # winter time, +1
        self.assertEqual(h.at(date(2026, 8, 10), 10), utc(2026, 8, 10, 8))  # August is +2 also when now is winter
        self.assertEqual(h.at(SPRING - ONE, 10), utc(2027, 3, 27, 9))
        self.assertEqual(h.at(SPRING, 10), utc(2027, 3, 28, 8))

    def test_repeated_and_missing_hour(self):
        h = self.h
        self.assertEqual(h.at(AUTUMN, 2, 30), utc(2026, 10, 25, 0, 30))     # the first 02:30 (+2)
        self.assertEqual(h.at(AUTUMN, 3, 0), utc(2026, 10, 25, 2, 0))
        self.assertEqual(h.at(SPRING, 2, 30), utc(2027, 3, 28, 1, 30))      # 02:30 does not exist: 03:30 (+2)
        self.assertEqual(h.at(SPRING_2026, 2, 30), utc(2026, 3, 29, 1, 30))

    def test_history_does_not_move(self):
        h = self.h
        for t, hm in ((utc(2026, 8, 10, 8), "10:00"), (utc(2026, 10, 25, 0, 30), "02:30"), (utc(2026, 10, 25, 1, 30), "02:30"),
                      (utc(2026, 10, 25, 2, 0), "03:00"), (utc(2027, 3, 28, 0, 59), "01:59"), (utc(2027, 3, 28, 1, 0), "03:00")):
            self.assertEqual(h.hhmm(t), hm, t)
        self.assertEqual(datetime.fromtimestamp(utc(2026, 10, 24, 22, 30), h.TZ).date(), AUTUMN)  # 00:30 local

    def test_the_time_zone_is_a_zone_not_an_offset(self):
        self.assertEqual(str(self.h.TZ), "Europe/Rome")
        self.assertEqual(self.h.summary(date(2026, 3, 10))["tz"], "Europe/Rome")


class BandSleepSummary(unittest.TestCase):
    """The band counts the minutes of its sleep summary from 24 hours before the day's midnight (Zepp: day + minutes
    - 1440; Gadgetbridge: timestampMidnight - 24 h + minutes): after a 25- or 23-hour day that is an hour away from
    the local midnight of the day before."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build_db(Path(cls.tmp.name) / "gb.db")
        cls.h = load_app(Path(cls.tmp.name) / "gb.db", HELIO_STATE=str(Path(cls.tmp.name) / "state"))

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def night(self, day, start, end):
        parsed = self.h.parse_sleep_blob(night_blob(day, start, end))
        self.assertEqual(parsed["day0"], local(day))
        return parsed

    def test_night_of_the_change_has_nine_hours(self):
        p = self.night(AUTUMN, local(AUTUMN - ONE, 23), local(AUTUMN, 7))
        self.assertEqual((p["start"], p["end"]), (utc(2026, 10, 24, 21), utc(2026, 10, 25, 6)))
        self.assertEqual(p["end"] - p["start"], 9 * 3600)
        self.assertEqual((self.h.hhmm(p["start"]), self.h.hhmm(p["end"])), ("23:00", "07:00"))

    def test_night_after_the_25_hour_day(self):
        p = self.night(AUTUMN + ONE, local(AUTUMN, 23), local(AUTUMN + ONE, 7))
        self.assertEqual((p["start"], p["end"]), (utc(2026, 10, 25, 22), utc(2026, 10, 26, 6)))
        self.assertEqual((self.h.hhmm(p["start"]), self.h.hhmm(p["end"])), ("23:00", "07:00"))
        self.assertEqual(p["stages"], [(p["start"], p["end"], "light")])

    def test_spring_nights(self):
        for day in (SPRING, SPRING_2026):
            p = self.night(day, local(day - ONE, 23), local(day, 7))
            self.assertEqual(p["end"] - p["start"], 7 * 3600)                 # the clock skipped an hour
            p = self.night(day + ONE, local(day, 23), local(day + ONE, 7))
            self.assertEqual((self.h.hhmm(p["start"]), self.h.hhmm(p["end"])), ("23:00", "07:00"))
            self.assertEqual(p["end"] - p["start"], 8 * 3600)

    def test_naps_too(self):
        blob = bytearray(night_blob(AUTUMN + ONE, local(AUTUMN, 23), local(AUTUMN + ONE, 7)))
        blob[0x13] = blob[0x14] = 1
        blob[0x17] = 1
        a, b = band_minutes(AUTUMN + ONE, local(AUTUMN, 15)), band_minutes(AUTUMN + ONE, local(AUTUMN, 15, 30))
        blob[0x18:0x1E] = a.to_bytes(2, "little") + (b - 1).to_bytes(2, "little") + (30).to_bytes(2, "little")
        nap = self.h.parse_sleep_blob(bytes(blob))["naps"][0]
        self.assertEqual((self.h.hhmm(nap["start"]), self.h.hhmm(nap["end"])), ("15:00", "15:30"))


class Days(unittest.TestCase):
    """A band worn around both changes: asleep from 23:00 to 07:00 local time every night."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        first_02, second_02 = local(AUTUMN, 2, 30), local(AUTUMN, 2, 30, fold=1)
        cls.late = local(AUTUMN, 23, 30)  # written after day0 + 24 h: only a 25-hour day has it
        build(d / "gb.db", [(date(2026, 10, 10), date(2026, 11, 5)), (date(2027, 3, 13), date(2027, 4, 8))],
              extra=[(first_02, 40, 80), (second_02, 70, 80)],
              late_summary=(cls.late * 1000, night_blob(AUTUMN, local(AUTUMN - ONE, 23), local(AUTUMN, 7), score=91,
                                                         written=cls.late)))
        cls.h = load_app(d / "gb.db", HELIO_STATE=str(d / "state"), HELIO_NOW=str(utc(2027, 4, 20, 12)))

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def setUp(self):
        self.h.clear_caches()

    def test_25_hour_day(self):
        s = self.h.summary(AUTUMN)
        self.assertEqual(s["day_end"] - s["day_start"], 25 * 3600)
        self.assertEqual(len(s["steps_by_hour"]), 25)
        self.assertEqual(s["hour_labels"], [0, 1, 2, 2] + list(range(3, 24)))
        self.assertEqual(s["steps_by_hour"][2], 40)                       # the first 02:30
        self.assertEqual(s["steps_by_hour"][3], 70)                       # the second 02:30
        self.assertEqual(sum(s["steps_by_hour"]), s["steps"])

    def test_23_hour_day(self):
        s = self.h.summary(SPRING)
        self.assertEqual(s["day_end"] - s["day_start"], 23 * 3600)
        self.assertEqual(s["hour_labels"], [0, 1] + list(range(3, 24)))
        self.assertEqual(sum(s["steps_by_hour"]), s["steps"])

    def test_every_minute_of_the_day_counts_once(self):
        with closing(self.h.connect()) as con:
            for d, minutes in ((AUTUMN, 1500), (SPRING, 1380), (AUTUMN + ONE, 1440)):
                self.assertEqual(self.h.compute_day(con, d, self.h.profile(con, d))["worn_min"], minutes, d)

    def test_basal_metabolism_of_the_hours_the_day_has(self):
        with closing(self.h.connect()) as con:
            for d, hours in ((AUTUMN, 25), (SPRING, 23), (AUTUMN + ONE, 24)):
                p = self.h.profile(con, d)
                day = self.h.compute_day(con, d, p)
                self.assertEqual(day["kcal"]["bmr"], round(p["bmr"] * hours / 24), d)
                # the day's energy shown everywhere is this one
                self.assertEqual(self.h.day_energy(day), {"kcal": day["kcal"]["total"], "range": day["kcal"]["range"],
                                                          "partial": False})

    def test_sleep_around_the_changes(self):
        with closing(self.h.connect()) as con:
            cases = ((AUTUMN, 9), (AUTUMN + ONE, 8), (SPRING, 7), (SPRING + ONE, 8), (AUTUMN - ONE, 8))
            for d, hours in cases:
                sl = self.h.sleep_for(con, d)
                self.assertEqual(sl["source"], "band", d)
                self.assertEqual((self.h.hhmm(sl["start"]), self.h.hhmm(sl["end"])), ("23:00", "07:00"), d)
                self.assertEqual(sl["total"], hours * 60, d)

    def test_last_summary_of_the_25th_hour(self):
        # the band rewrote the summary at 23:30 of the 25-hour day: the last one of the day wins
        with closing(self.h.connect()) as con:
            self.assertEqual(self.h.sleep_for(con, AUTUMN)["score"], 91)

    def test_regularity_compares_the_same_clock_time(self):
        with closing(self.h.connect()) as con:
            for d in (AUTUMN + 6 * ONE, SPRING + 6 * ONE):
                r = self.h.sleep_regularity(con, d)
                self.assertEqual(r["value"], 100, d)                       # same times every night, by the clock
                self.assertEqual(r["onset"], {"mean": 23 * 60, "sd": 0})
                self.assertEqual(r["wake"], {"mean": 7 * 60, "sd": 0})

    def test_clock_minutes(self):
        h = self.h
        autumn = h.clock_minutes(AUTUMN)
        self.assertEqual(len(autumn), 1440)
        self.assertEqual(autumn[150], utc(2026, 10, 25, 0, 30))           # 02:30: its first occurrence
        self.assertEqual(autumn[600], utc(2026, 10, 25, 9))               # 10:00 (+1)
        spring = h.clock_minutes(SPRING)
        self.assertEqual(spring[120:180], [None] * 60)                    # 02:00-02:59 does not exist
        self.assertEqual(spring[180], utc(2027, 3, 28, 1))
        self.assertEqual(h.clock_minutes(AUTUMN + ONE), list(range(h.midnight(AUTUMN + ONE), h.midnight(AUTUMN + 2 * ONE), 60)))

    def test_resting_heart_rate_on_its_local_day(self):
        with closing(self.h.connect()) as con:
            rm = self.h.resting_map(con)
        self.assertEqual(rm[AUTUMN], 52)
        self.assertEqual(rm[SPRING], 52)


class SleepFromSamples(unittest.TestCase):
    """Without the band's summary the night is read from 18:00 of the day before to 12:00, by the local clock."""

    def test_window_by_the_local_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # asleep until 11:45 on the 25-hour day: 12:00 local is 13 hours after midnight
            build(d / "gb.db", [(date(2026, 10, 23), date(2026, 10, 26))], no_summary=(AUTUMN,),
                  extra=[(t, 0, 120) for t in range(local(AUTUMN, 7), local(AUTUMN, 11, 45), 60)])
            h = load_app(d / "gb.db", HELIO_STATE=str(d / "state"), HELIO_NOW=str(utc(2026, 11, 1, 12)))
            try:
                with closing(h.connect()) as con:
                    sl = h.sleep_for(con, AUTUMN)
                self.assertEqual(sl["source"], "samples")
                self.assertEqual((h.hhmm(sl["start"]), h.hhmm(sl["end"])), ("23:00", "11:45"))
            finally:
                os.environ.pop("HELIO_STATE", None)


class NoFixedOffsets(unittest.TestCase):
    """No offset computed by hand: day lengths and local times come from zoneinfo (Python) or Intl (the page).
    Lines that really mean elapsed time (a duration, a cookie lifetime) say so with an "elapsed" comment."""
    FILES = ["helio_app.py", "helio_science.py", "helio_transfer.py", "helio_catalog.py", "helio_catalog_fetch.py",
             "static/app.js", "static/i18n.js"]
    SUSPECT = re.compile(r"86400|86_400|864e5|86400000|getTimezoneOffset|time\.timezone|time\.altzone|utcoffset\(|"
                         r"\*\s*3600|3600\s*\*|//\s*3600|%\s*86400")

    def test_no_fixed_day_or_offset(self):
        found = []
        for name in self.FILES:
            for n, line in enumerate((APP / name).read_text().splitlines(), 1):
                if self.SUSPECT.search(line) and "elapsed" not in line:
                    found.append(f"{name}:{n}: {line.strip()}")
        self.assertEqual(found, [])

    def test_the_check_sees_the_old_mistakes(self):
        for line in ("if (b <= a) b += 86400;", "base + 12 * 3600", "(day0 + 86400) * mult", "new Date().getTimezoneOffset()"):
            self.assertTrue(self.SUSPECT.search(line), line)


if __name__ == "__main__":
    unittest.main()
