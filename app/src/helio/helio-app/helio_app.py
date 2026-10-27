#!/usr/bin/env python3
"""Helio: local mini-app for Gadgetbridge data.

Read-only access to the database, no external dependencies, no requests to the Internet.
Calories, zones, load and readiness are estimates computed here with published formulas.
"""
import base64
from contextlib import closing
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import ssl
import statistics
import struct
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import helio_catalog as hc
import helio_science as sci

E = os.environ.get
DB = E("HELIO_DB", "/var/lib/gadgetbridge/Gadgetbridge.db")
# 1 = database in use by Gadgetbridge (app on the phone): no "immutable", the WAL is visible and writers are waited for
DB_LIVE = E("HELIO_DB_LIVE", "0") == "1"
DB_TIMEOUT = float(E("HELIO_DB_TIMEOUT", "5"))  # seconds to wait while Gadgetbridge writes (HELIO_DB_LIVE only)
DEVICE_ID = int(E("HELIO_DEVICE_ID", "1"))
BIND = E("HELIO_BIND", "127.0.0.1")
PORT = int(E("HELIO_PORT", "8080"))
def system_tz():
    """System time zone (HELIO_TZ, then TZ, /etc/timezone, /etc/localtime), otherwise UTC."""
    for name in (E("HELIO_TZ"), E("TZ")):
        if name:
            try:
                return ZoneInfo(name.lstrip(":"))
            except (KeyError, ValueError, OSError):
                pass
    try:
        name = Path("/etc/timezone").read_text().strip()
        if name:
            return ZoneInfo(name)
    except (OSError, KeyError, ValueError):
        pass
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return ZoneInfo(link.split("zoneinfo/", 1)[1])
    except (OSError, KeyError, ValueError):
        pass
    return ZoneInfo("UTC")


TZ = system_tz()
PLAN_FILE = E("HELIO_PLAN", "/etc/helio-plan.json")  # empty = no configuration plan
STATIC = Path(__file__).resolve().parent / "static"
TLS_BIND = E("HELIO_TLS_BIND", "")
TLS_PORT = int(E("HELIO_TLS_PORT", "443"))
TLS_CERT = E("HELIO_TLS_CERT", "/etc/helio/tls.crt")
TLS_KEY = E("HELIO_TLS_KEY", "/etc/helio/tls.key")
AUTH_USER = E("HELIO_USER", "helio")
AUTH_PASS = E("HELIO_PASSWORD", "")
LAN_OPEN = E("HELIO_LAN_OPEN", "0") == "1"  # 1 = no password from the home network
REMEMBER_DAYS = int(E("HELIO_REMEMBER_DAYS", "365"))
COOKIE = "helio_device"
STATE_DIR = Path(E("HELIO_STATE", "/var/lib/helio-app"))  # labels, plan, weight and strength log
# food catalog (community databases): its own folder next to the data, never inside it (see helio_catalog.py)
CATALOG_DIR = Path(E("HELIO_CATALOG", str(STATE_DIR.parent / (STATE_DIR.name + "-catalog"))))
CATALOG_BUNDLED = E("HELIO_CATALOG_BUNDLED", str(Path(__file__).resolve().parent / "catalog" / "generic.tar"))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- texts
# No visible text in the code: the server sends neutral messages {"$": key, ...params} that the page
# renders in the chosen language (static/i18n/<lang>.json), formatting dates and numbers there.
# Only where a finished text is needed (CSV, command line, import) tr() builds it, from the same
# catalogs.
I18N_DIR = STATIC / "i18n"
LANGS = ("it", "en")
_catalogs = {}


def M(key, **params):
    return {"$": key, **params}


def J(*parts):
    """Several sentences in one text (the page joins them with a space); empty parts are skipped."""
    return {"$": "+", "parts": [x for x in parts if x]}


class UserError(ValueError):
    """Error shown to the user: key and parameters, translated by the page."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.msg = M(key, **params)

    def __str__(self):
        return tr("en", self.msg["$"], **{k: v for k, v in self.msg.items() if k != "$"})


def catalog(lang):
    lang = lang if lang in LANGS else "en"
    if lang not in _catalogs:
        try:
            _catalogs[lang] = json.loads((I18N_DIR / f"{lang}.json").read_text())
        except (OSError, ValueError):
            _catalogs[lang] = {}
    return _catalogs[lang]


def _fmt_value(lang, v, spec):
    if isinstance(v, dict) and "$" in v:
        return tr(lang, v["$"], **{k: x for k, x in v.items() if k != "$"})
    if isinstance(v, (list, tuple)):
        return ", ".join(_fmt_value(lang, x, None) for x in v)
    if spec == "dur" and isinstance(v, (int, float)):
        h, m = divmod(int(round(v)), 60)
        return tr(lang, "fmt.dur_hm", h=h, m=f"{m:02d}") if h else tr(lang, "fmt.dur_m", m=m)
    if spec == "pct" and isinstance(v, (int, float)):
        return _fmt_value(lang, v, "0") + "%"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        digits = {"0": 0, "int": 0, "1": 1, "2": 2}.get(spec, 1)
        s = f"{round(v, digits):.{digits}f}" if digits else f"{int(round(v)):,}".replace(",", "_")
        if digits and "." in s:
            s = s.rstrip("0").rstrip(".")
        return s.replace(".", ",").replace("_", ".") if lang == "it" else s.replace("_", ",")
    return "–" if v is None else str(v)


def tr(lang, key, **params):
    """Finished text of a key (CSV, command line): same placeholders as the page, simple formats."""
    if key == "+":
        return " ".join(_fmt_value(lang, x, None) for x in params.get("parts", []))
    raw = catalog(lang).get(key)
    if raw is None:
        raw = catalog("en").get(key, key)
    if isinstance(raw, dict):
        raw = raw.get("one" if params.get("n") == 1 else "other", raw.get("other", key))
    return re.sub(r"\{(\w+)(?::(\w+))?\}", lambda m: _fmt_value(lang, params.get(m.group(1)), m.group(2)), raw)


def session_token():
    # Changes automatically when the password changes: every remembered device is signed out
    return hmac.new(AUTH_PASS.encode(), f"helio-session:{AUTH_USER}".encode(), "sha256").hexdigest()

ACT = "HUAMI_EXTENDED_ACTIVITY_SAMPLE"
HR_MIN, HR_MAX = 30, 220
ZONE_PCT = [0.50, 0.60, 0.70, 0.80, 0.90]  # thresholds in % of max HR

# Stage codes in the sleep summary computed by the band (HUAMI_SLEEP_SESSION_SAMPLE)
SLEEP_STAGE = {4: "light", 5: "deep", 7: "awake", 8: "rem"}
# Huami sample kinds (as in Gadgetbridge, HuamiExtendedSampleProvider)
KIND_NOT_WORN, KIND_CHARGING, KIND_SLEEP = 115, 118, 120
# Huami stress thresholds: 0-39 relax, 40-59 normal, 60-79 medium, 80-100 high (Zepp app 10.9.0, lbm.b).
# 0 is a valid value (Gadgetbridge stores it, Zepp counts it as relax); 0xFF = no measurement and never reaches the DB.
STRESS_BANDS = [("relax", 0, 39), ("light", 40, 59), ("moderate", 60, 79), ("high", 80, 100)]


def env_num(key, cast=float):
    v = E(key)
    try:
        return cast(v) if v not in (None, "") else None
    except ValueError:
        return None


def now():
    """Current time; HELIO_NOW (timestamp) lets tests fix it."""
    v = env_num("HELIO_NOW")
    return datetime.fromtimestamp(v, TZ) if v else datetime.now(TZ)


def at(d, h=0, m=0):
    """Instant of the local time h:m of day d, with the offset of that date (zoneinfo), never a fixed one.

    On the day the clock goes back (25 hours) a repeated time is its first occurrence; on the day it goes
    forward (23 hours) a time that does not exist becomes the one after the jump (02:30 -> 03:30).
    """
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp())


def midnight(d):
    return at(d)


def mean(v):
    return sum(v) / len(v) if v else None


def rnd(v, n=0):
    if v is None:
        return None
    return round(v, n) if n else int(round(v))


# ---------------------------------------------------------------- database

class Connection(sqlite3.Connection):
    """Read-only connection of one request, with the version of the database (db_mtime) read before its first query:
    what is computed with it is stored with a version never newer than the data it saw."""
    version = None


def connect():
    version = db_mtime()
    if not DB_LIVE:
        con = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True, factory=Connection)
    else:
        # live database: read-only with normal locks, so data still in the WAL is visible
        # and, if Gadgetbridge is writing, we wait up to DB_TIMEOUT seconds instead of failing
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=DB_TIMEOUT, factory=Connection)
        con.execute("PRAGMA query_only=1")
    con.version = version
    # one snapshot for the whole request: every query sees the same data, also while Gadgetbridge writes
    con.execute("BEGIN")
    return con


def con_version(con):
    v = getattr(con, "version", None)
    return v if v is not None else db_mtime()


def db_mtime():
    """Cache key: changes when the database data changes."""
    try:
        st = os.stat(DB)
    except OSError:
        return 0
    if not DB_LIVE:
        return st.st_mtime
    # in WAL mode writes stay in the -wal file until the checkpoint: the main file does not change
    try:
        w = os.stat(DB + "-wal")
        wal = (w.st_mtime_ns, w.st_size)
    except OSError:
        wal = None
    return (st.st_mtime_ns, st.st_size, wal)


class Memo:
    """Results by key, computed once also when several requests want them together (the page sends 9 at a time):
    the others wait for the first one instead of computing the same days again.

    version: the database version the result must belong to (connection's); a result of another version is used
    again only if still_valid(result) says its rows did not change, otherwise it is computed again.
    """

    def __init__(self, maxsize=800):
        self.maxsize = maxsize
        self._data = {}
        self._busy = {}
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._data.clear()

    def __len__(self):
        return len(self._data)

    def get(self, key, compute, version=None, still_valid=None):
        while True:
            with self._lock:
                hit = self._data.get(key)
                if hit is not None and hit[0] == version:
                    return hit[1]
                ev = self._busy.get(key)
                if ev is None:
                    ev = self._busy[key] = threading.Event()
                    break
            ev.wait()  # another request is computing it: its result will do
        try:
            if hit is not None and still_valid is not None and still_valid(hit[1]):
                value = hit[1]
            else:
                value = compute()
            with self._lock:
                if key not in self._data and len(self._data) >= self.maxsize:
                    for k in list(self._data)[:self.maxsize // 2]:  # the oldest half
                        del self._data[k]
                self._data[key] = (version, value)
            return value
        finally:
            with self._lock:
                self._busy.pop(key, None)
            ev.set()


_colcache = Memo(200)
_multcache = Memo(200)


def clear_caches():
    """Forgets every result computed from the database (tests, change of time zone)."""
    for c in (_colcache, _multcache, _restcache, _peakcache, _sleepcache, _daycache, _sricache, _profilecache,
              _statecache):
        c.clear()
    with _viewlock:
        _viewcache.clear()


def columns(con, table):
    def read():
        try:
            return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
        except sqlite3.Error:
            return set()
    return _colcache.get((DB, table), read, version=con_version(con))


def ts_mult(con, table):
    """1000 if the table's timestamps are in milliseconds, 1 if in seconds, None without rows."""
    if "TIMESTAMP" not in columns(con, table):
        return None

    def read():
        mx = con.execute(f'SELECT MAX(TIMESTAMP) FROM "{table}" WHERE DEVICE_ID=?', (DEVICE_ID,)).fetchone()[0]
        return 0 if mx is None else 1000 if mx > 10**11 else 1
    return _multcache.get((DB, table), read, version=con_version(con)) or None


# Rows a result was computed from: if they are the same (how many, the last time, the sum of their values), the
# result is still right after Gadgetbridge wrote other rows. A sync brings today's data: the past days stay.
SIG_SUMS = {
    ACT: ("STEPS", "HEART_RATE", "RAW_KIND", "RAW_INTENSITY", "DEEP_SLEEP", "REM_SLEEP"),
    "HUAMI_SLEEP_SESSION_SAMPLE": ("LENGTH(DATA)",),
    "HUAMI_HEART_RATE_RESTING_SAMPLE": ("HEART_RATE",),
    "GENERIC_HRV_VALUE_SAMPLE": ("VALUE",),
    "HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE": ("RATE",),
    "HUAMI_SPO2_SAMPLE": ("SPO2",),
    "GENERIC_TEMPERATURE_SAMPLE": ("TEMPERATURE",),
}


def table_sig(con, table, start, end):
    """Signature of a table's rows between start (included) and end (excluded), in seconds."""
    cols = columns(con, table)
    if table == "BASE_ACTIVITY_SUMMARY":
        if not {"START_TIME", "END_TIME"} <= cols:
            return None
        dev = " AND DEVICE_ID=?" if "DEVICE_ID" in cols else ""
        name = 'TOTAL(LENGTH("NAME"))' if "NAME" in cols else "0"
        return con.execute(f'SELECT COUNT(*), MAX(START_TIME), TOTAL(END_TIME), {name} FROM "BASE_ACTIVITY_SUMMARY" '
                           f'WHERE ((START_TIME>=? AND START_TIME<?) OR (START_TIME>=? AND START_TIME<?)){dev}',
                           (start, end, start * 1000, end * 1000) + ((DEVICE_ID,) if dev else ())).fetchone()
    if not {"TIMESTAMP", "DEVICE_ID"} <= cols:
        return None
    mult = ts_mult(con, table)
    if mult is None:
        return None
    sums = "".join(f", TOTAL({c})" for c in SIG_SUMS.get(table, ()) if c.removeprefix("LENGTH(").rstrip(")") in cols)
    return con.execute(f'SELECT COUNT(*), MAX(TIMESTAMP){sums} FROM "{table}" WHERE DEVICE_ID=? AND TIMESTAMP>=? '
                       'AND TIMESTAMP<?', (DEVICE_ID, start * mult, end * mult)).fetchone()


def data_sig(con, ranges):
    return tuple(table_sig(con, t, a, b) for t, a, b in ranges)


def by_data(memo, key, con, compute, ranges):
    """compute() stored with the rows it read (ranges(result) -> [(table, start, end), ...]) and used again, also
    after the database changed, as long as those rows are the same."""
    def fresh():
        res = compute()
        rr = ranges(res)
        return {"result": res, "ranges": rr, "sig": data_sig(con, rr)}
    return memo.get(key, fresh, version=con_version(con),
                    still_valid=lambda e: data_sig(con, e["ranges"]) == e["sig"])["result"]


def now_version(con):
    """Version of today's results: the database's and the minute. The requests of the same minute (the page sends
    them together) share one computation; the entry is replaced, not added, every minute."""
    return con_version(con), int(now().timestamp()) // 60


def series(con, table, col, start, end, lo=None, hi=None):
    """Values (timestamp in seconds, value) of a table between start (included) and end (excluded)."""
    if not {"TIMESTAMP", "DEVICE_ID", col} <= columns(con, table):
        return []
    mult = ts_mult(con, table)
    if mult is None:
        return []
    rows = con.execute(
        f'SELECT TIMESTAMP, "{col}" FROM "{table}" WHERE DEVICE_ID=? AND TIMESTAMP>=? AND TIMESTAMP<? ORDER BY TIMESTAMP',
        (DEVICE_ID, start * mult, end * mult),
    ).fetchall()
    return [(int(t // mult), v) for t, v in rows
            if v is not None and (lo is None or v >= lo) and (hi is None or v <= hi)]


def latest(con, table, col, lo=None, hi=None, before=None):
    if not {"TIMESTAMP", "DEVICE_ID", col} <= columns(con, table):
        return None
    mult = ts_mult(con, table)
    if mult is None:
        return None
    cond, args = "", [DEVICE_ID]
    if before is not None:
        cond, args = " AND TIMESTAMP<?", [DEVICE_ID, before * mult]
    rows = con.execute(
        f'SELECT TIMESTAMP, "{col}" FROM "{table}" WHERE DEVICE_ID=?{cond} ORDER BY TIMESTAMP DESC LIMIT 50', args
    ).fetchall()
    for t, v in rows:
        if v is not None and (lo is None or v >= lo) and (hi is None or v <= hi):
            return {"t": int(t // mult), "v": v}
    return None


def activity_rows(con, start, end):
    cols = columns(con, ACT)
    if not {"TIMESTAMP", "STEPS", "HEART_RATE"} <= cols:
        return []
    opt = [c for c in ("RAW_INTENSITY", "RAW_KIND", "SLEEP", "DEEP_SLEEP", "REM_SLEEP") if c in cols]
    sel = ", ".join(["TIMESTAMP", "STEPS", "HEART_RATE"] + [f'"{c}"' for c in opt])
    rows = con.execute(
        f'SELECT {sel} FROM "{ACT}" WHERE DEVICE_ID=? AND TIMESTAMP>=? AND TIMESTAMP<? ORDER BY TIMESTAMP',
        (DEVICE_ID, start, end),
    ).fetchall()
    out = []
    for r in rows:
        d = {"t": r[0], "raw_steps": r[1] if r[1] is not None else 0,
             "steps": r[1] if r[1] and r[1] > 0 else 0,
             "hr": r[2] if r[2] and HR_MIN <= r[2] <= HR_MAX else None}
        for i, c in enumerate(opt):
            d[c] = r[3 + i] if r[3 + i] is not None else 0
        out.append(d)
    return clean_hr(out)


# ---------------------------------------------------------------- data cleaning
# Only measurement errors that can be recognised with certainty are removed. Every rule has its source
# (see CLEAN_RULES) and is checked by the tests, including a check that it leaves real data alone.

SPIKE_BPM = 25          # minimum jump over the two neighbouring minutes to call it an artefact
SPIKE_AGREE = 12        # the two neighbouring minutes must agree with each other
MOVE_STEPS = 20         # steps (previous minute + same minute) that justify a fast rise
MOVE_INTENSITY = 102    # 0.4 x 256: same "active minute" threshold used by Gadgetbridge
SPO2_NEIGHBOR_S = 900   # nearby SpO2 readings: within 15 minutes
SPO2_DIP = 3            # drop in percentage points (AASM desaturation threshold)
BASE_DAYS = 60          # baseline window (AIS)
BASE_MIN = 14           # minimum days for the baseline
ROLL_MIN = 3            # minimum measurements in 7 days for the rolling average (AIS)


def is_moving(prev, row):
    steps = (prev["steps"] if prev else 0) + row["steps"]
    return steps >= MOVE_STEPS or row.get("RAW_INTENSITY", 0) >= MOVE_INTENSITY


def clean_hr(rows):
    """Removes heart rate values that are optical sensor errors.

    - spike: a single minute at least 25 bpm above both neighbouring minutes (which agree with each other)
      with no steps or movement to explain it;
    - drop: a single minute at least 25 bpm below both neighbours (loss of contact).
    The original value stays in hr_raw: Gadgetbridge and its sessions use that one.
    """
    for r in rows:
        r["hr_raw"] = r["hr"]
    for i in range(1, len(rows) - 1):
        a, r, b = rows[i - 1], rows[i], rows[i + 1]
        ha, h, hb = a["hr_raw"], r["hr_raw"], b["hr_raw"]
        if ha is None or h is None or hb is None:
            continue
        if r["t"] - a["t"] != 60 or b["t"] - r["t"] != 60 or abs(ha - hb) > SPIKE_AGREE:
            continue
        if h - max(ha, hb) >= SPIKE_BPM and not is_moving(a, r):
            r["hr"], r["hr_fix"] = None, "picco"
        elif min(ha, hb) - h >= SPIKE_BPM:
            r["hr"], r["hr_fix"] = None, "caduta"
    return rows


def hr_fixes(rows):
    return [{"t": r["t"], "v": r["hr_raw"], "why": r["hr_fix"]} for r in rows if r.get("hr_fix")]


def spo2_clean(vals, rows):
    """Cleaned SpO2 readings.

    - readings taken while moving are discarded (motion artefacts, more frequent at the wrist);
    - the reported minimum is the lowest level held by two consecutive readings (within 15 minutes);
    - an isolated reading at least 3 points below that minimum is within the stated error of wrist
      sensors and, with one sample every 5 minutes, cannot be told apart from a real desaturation: it is
      counted separately (isolated dips) instead of becoming the minimum, so it does not disappear.
    """
    by_min = {r["t"]: r for r in rows}
    kept, motion = [], 0
    for t, v in vals:
        m = t // 60 * 60
        r = by_min.get(m)
        if r is not None and is_moving(by_min.get(m - 60), r):
            motion += 1
        elif r is not None and r["steps"] > 0:
            motion += 1
        else:
            kept.append((t, v))
    if not vals:
        return None
    if not kept:
        return {"avg": None, "min": None, "max": None, "min_raw": min(v for _, v in vals), "n": len(vals),
                "motion_removed": motion, "isolated_dips": 0, "confirmed": False}
    pairs = []
    for i, (t, v) in enumerate(kept[:-1]):
        if kept[i + 1][0] - t <= SPO2_NEIGHBOR_S:
            pairs.append(max(v, kept[i + 1][1]))
    vs = [v for _, v in kept]
    low = min(pairs) if pairs else min(vs)
    dips = sum(1 for v in vs if v <= low - SPO2_DIP)
    return {"avg": rnd(mean(vs)), "min": low, "max": max(vs),
            "min_raw": min(v for _, v in vals), "n": len(vals), "motion_removed": motion,
            "isolated_dips": dips, "confirmed": bool(pairs)}


def baseline(values, log=False, floor=0.0):
    """Personal baseline as in the AIS guidelines (Plews 2013, Buchheit 2014).

    values: one value per day (None if missing), from the oldest to today, up to 60 days.
    - 7-day rolling average if there are at least 3 measurements;
    - personal normal range = 60-day mean ± SWC, with SWC = 0.5 x standard deviation
      (on ln for HRV, where the standard deviation of ln equals the coefficient of variation);
    - floor: minimum SWC equal to the resolution of the measurement.
    """
    tr = math.log if log else (lambda x: x)
    inv = math.exp if log else (lambda x: x)
    vals = [v for v in values if v]
    last7 = [v for v in values[-7:] if v]
    out = {"today": values[-1] if values else None, "n7": len(last7), "n_base": len(vals), "need": BASE_MIN,
           "avg7": None, "mean": None, "low": None, "high": None, "status": "collecting"}
    if len(last7) >= ROLL_MIN:
        out["avg7"] = round(inv(mean([tr(v) for v in last7])), 1)
    if len(vals) >= BASE_MIN:
        t = [tr(v) for v in vals]
        m, sd = mean(t), statistics.stdev(t)
        swc = max(0.5 * sd, floor)
        out.update({"mean": round(inv(m), 1), "low": round(inv(m - swc), 1), "high": round(inv(m + swc), 1),
                    "swc": round(swc, 3)})
        if out["avg7"] is not None:
            a = tr(out["avg7"])
            out["status"] = "above" if a > m + swc else "below" if a < m - swc else "normal"
    return out


# cleaning rules: title and text in the catalogs (rule.<id>.title / rule.<id>.text)
CLEAN_RULES = [
    {"id": "hr_spike", "sources": ["bent", "fuller"]},
    {"id": "spo2_motion", "sources": ["walzel"]},
    {"id": "spo2_min", "sources": ["walzel", "aasm"]},
    {"id": "baseline", "sources": ["ais_hrv", "vesterinen"]},
]


def rows_dicts(con, sql, args=()):
    cur = con.execute(sql, args)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------- profile

# Profile entered by hand in the Profile sheet: it stays only in the data folder (profile.json), takes
# precedence over the configuration and Gadgetbridge and changes every calculation at once. An empty field
# goes back to the previous value. Every value must be in a plausible range for an adult or a teenager.
PROFILE_NUMBERS = {
    # field: (type, minimum, maximum, unit key, name key in the messages)
    "height_cm": (float, 100, 250, "u.cm", "pf.height_cm"),
    "hrmax": (int, 100, 230, "u.bpm", "pf.hrmax"),
    "rest_hr": (int, 30, 110, "u.bpm", "pf.rest_hr"),
    "stride_m": (float, 0.3, 1.2, "u.m", "pf.stride_m"),
    "steps_goal": (int, 1000, 50000, "u.steps", "pf.steps_goal"),
    "sleep_goal_min": (int, 240, 720, "u.min", "pf.sleep_goal_min"),
    "active_goal": (int, 10, 300, "u.min", "pf.active_goal"),
    "zone_goal_week": (int, 30, 1500, "u.min", "pf.zone_goal_week"),
    # optional: an altitude to reach (for example a summit) for the VO2max estimate at altitude
    "altitude_goal_m": (int, 500, 8849, "u.m", "pf.altitude_goal_m"),
}
PROFILE_AGE = (10, 110)  # allowed ages for the birth date
PROFILE_NAME_MAX = 40    # name of the altitude goal
PROFILE_FIELDS = ("birth_date", "sex", "altitude_goal_name", "work_level") + tuple(PROFILE_NUMBERS)


def _profile_number(key, v):
    cast, lo, hi, unit, name = PROFILE_NUMBERS[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise UserError("err.need_number", field=M(name))
    if not lo <= v <= hi:
        raise UserError("err.range", field=M(name), lo=lo, hi=hi, unit=M(unit))
    if cast is int:
        if v != int(v):
            raise UserError("err.need_integer", field=M(name))
        return int(v)
    return round(float(v), 2 if key == "stride_m" else 1)


def age_on(born, d):
    return d.year - born.year - ((d.month, d.day) < (born.month, born.day))


def validate_profile(data):
    """Profile from the page: only the expected fields, each within its range. None or "" = field removed."""
    if not isinstance(data, dict):
        raise UserError("err.profile_invalid")
    unknown = sorted(set(data) - set(PROFILE_FIELDS))
    if unknown:
        raise UserError("err.unknown_field", field=unknown[0])
    out = {}
    for key, v in data.items():
        if v is None or v == "":
            continue
        if key == "birth_date":
            if not isinstance(v, str) or not DATE_RE.match(v):
                raise UserError("err.birth_invalid")
            try:
                born = date.fromisoformat(v)
            except ValueError:
                raise UserError("err.birth_invalid") from None
            age = age_on(born, now().date())
            if not PROFILE_AGE[0] <= age <= PROFILE_AGE[1]:
                raise UserError("err.birth_range", lo=PROFILE_AGE[0], hi=PROFILE_AGE[1])
            out[key] = v
        elif key == "sex":
            if v not in ("M", "F"):
                raise UserError("err.sex_invalid")
            out[key] = v
        elif key == "altitude_goal_name":
            if not isinstance(v, str) or not v.strip() or len(v) > PROFILE_NAME_MAX or not v.isprintable():
                raise UserError("err.name_invalid", max=PROFILE_NAME_MAX)
            out[key] = v.strip()
        elif key == "work_level":
            if v not in WORK_LEVELS:
                raise UserError("err.work_level_invalid")
            out[key] = v
        else:
            out[key] = _profile_number(key, v)
    return out


def profile_load():
    """Profile saved by the app; a damaged or out-of-range value is ignored, the others stay."""
    out = {}
    for key, v in state_load("profile.json").items():
        if key in PROFILE_FIELDS:
            try:
                out.update(validate_profile({key: v}))
            except ValueError:
                pass
    return out


def save_profile(body):
    if body.get("reset"):
        with _labels_lock:
            state_delete("profile.json")
        return {"ok": True}
    clean = validate_profile(body.get("profile"))
    with _labels_lock:
        if clean:
            state_save("profile.json", clean)
        else:
            state_delete("profile.json")
    return {"ok": True}


def profile_payload():
    with closing(connect()) as con:
        p = profile(con)
    limits = {k: {"min": v[1], "max": v[2], "unit": v[3]} for k, v in PROFILE_NUMBERS.items()}
    limits["age"] = {"min": PROFILE_AGE[0], "max": PROFILE_AGE[1], "unit": "u.years"}
    return {"saved": profile_load(), "limits": limits,
            "effective": {k: p[k] for k in ("age", "sex", "height", "weight", "hrmax", "hrmax_src", "hrmax_pred",
                                            "stride_m", "steps_goal", "sleep_goal_min", "active_goal",
                                            "zone_goal_week", "bmr", "rest_hr", "altitude_goal_m",
                                            "altitude_goal_name", "work_level")},
            "work_levels": work_levels(),
            "base": p["base"], "src": p["src"]}


def weights_load():
    """Weight log entered in the app: {date: kg}."""
    out = {}
    for k, v in state_load("weights.json").items():
        if isinstance(k, str) and DATE_RE.match(k) and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v)
    return out


def weight_for(d, weights=None):
    """Last weight logged up to and including day d: (date, kg) or None."""
    weights = weights if weights is not None else state_view("weights.json", weights_load)
    best = None
    for k in weights:
        if k <= d.isoformat() and (best is None or k > best):
            best = k
    return (best, weights[best]) if best else None


_restcache = Memo(4)


def resting_map(con):
    """Resting heart rate computed by the band, one value per day (the last of the day)."""
    def read():
        m = {}
        for t, v in series(con, "HUAMI_HEART_RATE_RESTING_SAMPLE", "HEART_RATE", 0, 2 ** 31, HR_MIN, HR_MAX):
            m[datetime.fromtimestamp(t, TZ).date()] = v
        return m
    return _restcache.get(DB, read, version=con_version(con))


PEAK_MIN = 150          # peaks below this value cannot exceed the age-predicted max HR
CADENCE_LOCK = 3        # heart rate equal (±3) to the steps of the minute: the sensor locked onto the cadence
_peakcache = Memo(4)


def hr_peaks(con):
    """Daily heart rate peak held for 2 consecutive minutes while moving.

    A single minute can be an optical sensor artefact (errors grow with movement):
    the peak counts only if two minutes in a row are both high, with steps or wrist movement, outside
    sleep and without the heart rate "locked" onto the step cadence.
    """
    return _peakcache.get(DB, lambda: _hr_peaks(con), version=con_version(con))


def _hr_peaks(con):
    cols = columns(con, ACT)
    out = {}
    if {"TIMESTAMP", "HEART_RATE", "STEPS"} <= cols:
        opt = [c for c in ("RAW_INTENSITY", "RAW_KIND") if c in cols]
        sel = ", ".join(["TIMESTAMP", "HEART_RATE", "STEPS"] + [f'"{c}"' for c in opt])
        rows = con.execute(f'SELECT {sel} FROM "{ACT}" WHERE DEVICE_ID=? AND HEART_RATE>=? AND HEART_RATE<=? '
                           'ORDER BY TIMESTAMP', (DEVICE_ID, PEAK_MIN, HR_MAX)).fetchall()
        recs = []
        for r in rows:
            rec = {"t": r[0], "hr": r[1], "steps": max(0, r[2] or 0)}
            for i, c in enumerate(opt):
                rec[c] = r[3 + i] or 0
            recs.append(rec)

        def ok(x):
            if x.get("RAW_KIND") in (KIND_SLEEP, KIND_NOT_WORN, KIND_CHARGING):
                return False
            return not (x["steps"] >= 100 and abs(x["hr"] - x["steps"]) <= CADENCE_LOCK)

        for a, b in zip(recs, recs[1:]):
            if b["t"] - a["t"] != 60 or not ok(a) or not ok(b):
                continue
            moving = a["steps"] + b["steps"] >= MOVE_STEPS or max(a.get("RAW_INTENSITY", 0), b.get("RAW_INTENSITY", 0)) >= MOVE_INTENSITY
            if not moving:
                continue
            v = min(a["hr"], b["hr"])
            day = datetime.fromtimestamp(a["t"], TZ).date()
            if v > out.get(day, (0, 0))[0]:
                out[day] = (v, a["t"])
    return out


def hrmax_info(con, age, fixed=None):
    """Personal max HR: set by hand, or the highest of the age estimate (Tanaka 2001)
    and the peak observed in the last 2 years. An observed peak cannot exceed the true max HR,
    so it raises the estimate but never lowers it."""
    pred = round(208 - 0.7 * age)
    fixed = fixed or env_num("HELIO_HRMAX", int)
    since = now().date() - timedelta(days=730)
    peaks = [(v, t) for day, (v, t) in hr_peaks(con).items() if day >= since]
    obs = max(peaks) if peaks else None
    info = {"predicted": pred, "observed": obs[0] if obs else None, "observed_at": obs[1] if obs else None}
    if fixed:
        info.update(value=fixed, src="set")
    elif obs and obs[0] > pred:
        info.update(value=obs[0], src="observed")
    else:
        info.update(value=pred, src="age")
    return info


def vo2max_for(con, d, hrmax):
    """VO2max estimated with the Uth 2004 method from the band's resting heart rate (median of the last 7 days)."""
    rm = resting_map(con)
    vals = [rm[d - timedelta(days=i)] for i in range(7) if (d - timedelta(days=i)) in rm]
    if len(vals) < 3:
        return None, None, len(vals)
    rest = statistics.median(vals)
    return round(sci.uth_vo2max(hrmax, rest), 1), rest, len(vals)


PROFILE_ENV = ("HELIO_AGE", "HELIO_WEIGHT", "HELIO_HEIGHT", "HELIO_STEP_GOAL", "HELIO_SLEEP_GOAL", "HELIO_SEX",
               "HELIO_ACTIVE_GOAL", "HELIO_ZONE_GOAL", "HELIO_STRIDE_M", "HELIO_HRMAX", "HELIO_GOAL_ALT", "HELIO_GOAL_NAME")
_profilecache = Memo(400)


def profile(con, d=None):
    """Profile for day d (today if missing): the weight follows the log and the basal metabolism follows it.
    Computed once per day for each version of the database, of the profile and of the weights."""
    d = d or now().date()
    key = (DB, d.isoformat(), now().date().isoformat(), state_version("profile.json"), state_version("weights.json"),
           tuple(E(k) for k in PROFILE_ENV))
    return dict(_profilecache.get(key, lambda: _profile(con, d), version=con_version(con)))


def _profile(con, d):
    p = {"age": None, "sex": None, "weight": None, "height": None,
         "steps_goal": None, "sleep_goal_min": None}
    src = {}
    weight_src = None
    try:
        if columns(con, "USER"):
            u = rows_dicts(con, 'SELECT * FROM "USER" LIMIT 1')
            if u:
                u = u[0]
                b = u.get("BIRTHDAY")
                if b:
                    b = b / 1000 if b > 10**11 else b
                    born = datetime.fromtimestamp(b, TZ).date()
                    p["age"] = age_on(born, now().date())
                p["sex"] = {0: "F", 1: "M"}.get(u.get("GENDER"))
        if columns(con, "USER_ATTRIBUTES"):
            attrs = rows_dicts(con, 'SELECT * FROM "USER_ATTRIBUTES"')
            if attrs:
                a = max(attrs, key=lambda x: x.get("VALID_FROM_UTC") or 0)
                p["weight"] = a.get("WEIGHT_KG") or None
                weight_src = "Gadgetbridge" if p["weight"] else None
                p["height"] = a.get("HEIGHT_CM") or None
                p["steps_goal"] = a.get("STEPS_GOAL_SPD") or None
                if a.get("SLEEP_GOAL_MPD"):
                    p["sleep_goal_min"] = int(a["SLEEP_GOAL_MPD"])
                elif a.get("SLEEP_GOAL_HPD"):
                    p["sleep_goal_min"] = int(a["SLEEP_GOAL_HPD"] * 60)
    except sqlite3.Error:
        pass
    for k, v in p.items():
        if v:
            src[k] = "Gadgetbridge"

    # values in /etc/helio-app.env take precedence over Gadgetbridge
    for key, env, cast in (("age", "HELIO_AGE", int), ("weight", "HELIO_WEIGHT", float),
                           ("height", "HELIO_HEIGHT", float), ("steps_goal", "HELIO_STEP_GOAL", int),
                           ("sleep_goal_min", "HELIO_SLEEP_GOAL", int)):
        v = env_num(env, cast)
        if v:
            p[key] = v
            src[key] = "configurazione"
            if key == "weight":
                weight_src = "configurazione"
    if E("HELIO_SEX") in ("M", "F"):
        p["sex"], src["sex"] = E("HELIO_SEX"), "configurazione"
    extra = {"active_goal": env_num("HELIO_ACTIVE_GOAL", int), "zone_goal_week": env_num("HELIO_ZONE_GOAL", int),
             "stride_m": env_num("HELIO_STRIDE_M"), "hrmax": env_num("HELIO_HRMAX", int), "rest_hr": None,
             "altitude_goal_m": env_num("HELIO_GOAL_ALT", int), "altitude_goal_name": E("HELIO_GOAL_NAME") or None}
    for k, v in extra.items():
        if v:
            src[k] = "configurazione"
    # starting values, without the app profile: the page shows them as hints in empty fields
    base = {"age": p["age"], "sex": p["sex"], "height_cm": p["height"], "steps_goal": p["steps_goal"],
            "sleep_goal_min": p["sleep_goal_min"], **{k: v for k, v in extra.items()}}

    # the profile entered in the app takes precedence over everything (weight has its own log, below)
    app = state_view("profile.json", profile_load)
    if app.get("birth_date"):
        p["age"], src["age"] = age_on(date.fromisoformat(app["birth_date"]), now().date()), "app"
    for key, field in (("sex", "sex"), ("height", "height_cm"), ("steps_goal", "steps_goal"),
                       ("sleep_goal_min", "sleep_goal_min")):
        if field in app:
            p[key], src[key] = app[field], "app"
    for key in ("active_goal", "zone_goal_week", "stride_m", "hrmax", "rest_hr", "altitude_goal_m", "altitude_goal_name"):
        if key in app:
            extra[key], src[key] = app[key], "app"
    # intensity of the work activities without their own choice
    p["work_level"] = app["work_level"] if app.get("work_level") in WORK_LEVELS else WORK_DEFAULT
    src["work_level"] = "app" if app.get("work_level") in WORK_LEVELS else "predefinito"
    # the app's weight log applies from the day it was entered
    w = weight_for(d)
    if w:
        p["weight"], weight_src = w[1], "log"
        p["weight_date"] = w[0]

    p["missing"] = [k for k in ("age", "sex", "weight", "height") if not p[k]]
    p["age"] = p["age"] or 30
    p["weight"] = p["weight"] or 75
    p["weight_src"] = weight_src or "predefinito"
    p["height"] = p["height"] or 175
    p["steps_goal"] = p["steps_goal"] or 10000
    p["sleep_goal_min"] = p["sleep_goal_min"] or 480
    p["active_goal"] = extra["active_goal"] or 60
    p["zone_goal_week"] = extra["zone_goal_week"] or 150
    # stride from height (0.415 × height, classic walking estimate) if not given
    p["stride_m"] = extra["stride_m"] or round(p["height"] * 0.415 / 100, 2)
    p["rest_hr"] = extra["rest_hr"]
    p["altitude_goal_m"], p["altitude_goal_name"] = extra["altitude_goal_m"], extra["altitude_goal_name"]
    for k in ("age", "sex", "weight", "height", "steps_goal", "sleep_goal_min", "active_goal", "zone_goal_week",
              "stride_m", "hrmax", "rest_hr"):
        src.setdefault(k, "predefinito")
    if weight_src == "log":
        src["weight"] = "log"
    p["src"], p["base"] = src, base
    hm = hrmax_info(con, p["age"], extra["hrmax"])
    p["hrmax"], p["hrmax_src"], p["hrmax_pred"] = hm["value"], hm["src"], hm["predicted"]
    p["hrmax_obs"], p["hrmax_obs_at"] = hm["observed"], hm["observed_at"]
    # Mifflin-St Jeor (Madden 2016: the most accurate, within ±10% in 66% of cases)
    s = {"M": 5, "F": -161}.get(p["sex"], -78)
    p["bmr"] = round(10 * p["weight"] + 6.25 * p["height"] - 5 * p["age"] + s)
    vo2, rest, n = vo2max_for(con, d, p["hrmax"])
    p["vo2max_est"], p["vo2max_rest"] = vo2, rest
    # calories still need a VO2max: without the band's resting heart rate the profile one is used, or 60 bpm
    p["vo2max"] = vo2 or round(sci.uth_vo2max(p["hrmax"], p["rest_hr"] or 60), 1)
    return p


def load_plan():
    """Weekly plan: the one edited in the app (data folder) or, if missing, the configuration file."""
    plan = state_load("plan.json", None)
    if isinstance(plan, dict) and plan:
        return plan
    if not PLAN_FILE:
        return {}
    try:
        plan = json.loads(Path(PLAN_FILE).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(plan, dict):
        return {}
    migrate_codes(plan)  # the configuration file is not rewritten
    return plan


def plan_version():
    try:
        conf = os.stat(PLAN_FILE).st_mtime_ns if PLAN_FILE else 0
    except OSError:
        conf = 0
    return (state_version("plan.json"), conf)


# ---------------------------------------------------------------- activity (as in Gadgetbridge)

def gb_sessions(rows, stride_m, min_len=300, max_idle=300, min_spm=40, run_spm=120):
    """Faithful port of Gadgetbridge's StepAnalysis.calculateStepSessions.

    A session starts with any sample, accumulates the minutes with more than 40 steps
    (or intensity > 0.4 and at least one step) and closes after a 5-minute pause.
    Only sessions of at least 5 minutes are kept. The kind follows the same rules as Gadgetbridge.
    """
    min_int = max(0.0, min(1.0, min_spm * 0.01))
    out, start, prev = [], None, None
    act = idle = sb = 0
    ai = ib = 0.0
    hrs, hb = [], []

    def close(end_t, idle_t, last_t):
        length = last_t - start - idle_t
        if length < min_len:
            return
        hr_avg = sum(hrs) // len(hrs) if hrs else 0
        spm = act / (length // 60)
        if spm > run_spm:
            kind = "running"
        elif act > 200:
            kind = "walking"
        elif hr_avg > 90 and ai > 15:
            kind = "exercise"
        else:
            kind = "activity"
        out.append({"start": start, "end": end_t, "steps": act, "avg_hr_gb": hr_avg,
                    "distance_km": round(act * stride_m / 1000, 2), "kind": kind})

    for r in rows:
        st = r.get("raw_steps", r["steps"])
        inten = r.get("RAW_INTENSITY", 0) / 256.0
        hr = r.get("hr_raw", r["hr"])
        if start is None:
            start, prev = r["t"], None
            act, ai = max(st, 0), inten
            hrs = [hr] if hr else []
            idle = sb = 0
            ib = 0.0
            hb = []
        if prev is not None:
            dt = r["t"] - prev
            if st > min_spm or (inten > min_int and st > 0):
                act += st + sb
                ai += inten + ib
                if hr:
                    hrs.append(hr)
                hrs += hb
                hb, sb, ib, idle = [], 0, 0.0, 0
            else:
                if st >= 0:
                    sb += st
                if hr:
                    hb.append(hr)
                idle += dt
                ib += inten
            if idle >= max_idle:
                close(r["t"] - idle, idle, r["t"])
                start = None
        prev = r["t"]
    if start is not None and prev is not None:
        close(prev, idle, prev)
    return out


# Gadgetbridge session names (keys kind.<type>)
KIND_NAMES = {k: M(f"kind.{k}") for k in ("running", "walking", "exercise", "activity")}


def zone_of(hr, hrmax):
    pct = hr / hrmax
    z = -1
    for i, th in enumerate(ZONE_PCT):
        if pct >= th:
            z = i
    return z  # -1 = below zone 1


DEFAULT_KCAL_METHOD = M("kcal.default_method")


def kcal_active_minute(r, p, hr_threshold):
    """Active calories (above basal metabolism) of one minute.

    - Walking: cost per metre from the LCDA equation (Looney 2019, meta-regression of 48 studies).
    - From 40% of heart rate reserve up (ACSM moderate intensity): heart rate reserve -> oxygen reserve
      (Ferri Marini 2021) with the estimated VO2max, then kcal (Weir 1949). Below that threshold heart rate depends
      too much on posture, heat and emotions to estimate calories, so only walking counts.
    """
    walk = sci.walk_kcal(r["steps"], p["stride_m"], p["weight"])
    hr = r["hr"]
    if hr is not None and hr >= hr_threshold:
        return max(sci.hr_active_kcal(hr, p.get("rest") or 60, p["hrmax"], p["vo2max"], p["bmr"], p["weight"]), walk)
    return walk


def describe_session(sess, rows, p, hr_threshold):
    part = [r for r in rows if sess["start"] <= r["t"] <= sess["end"]]
    hrs = [r["hr"] for r in part if r["hr"] is not None]
    zones = [0] * 5
    for h in hrs:
        z = zone_of(h, p["hrmax"])
        if z >= 0:
            zones[z] += 1
    sess.update({
        "name": sess.get("name") or KIND_NAMES.get(sess.get("kind"), KIND_NAMES["activity"]),
        "source": sess.get("source", "gadgetbridge"),
        "duration_min": round((sess["end"] - sess["start"]) / 60),
        "avg_hr": (round(mean(hrs)) if hrs else None) if (not sess.get("avg_hr_gb") or any(r.get("hr_fix") for r in part))
        else sess["avg_hr_gb"],
        "hr_fixed": sum(1 for r in part if r.get("hr_fix")),
        "max_hr": max(hrs) if hrs else None,
        "kcal": round(sum(kcal_active_minute(r, p, hr_threshold) for r in part)),
        "zones": zones,
        "trimp": round(sci.banister_trimp(hrs, p.get("rest") or 60, p["hrmax"], p["sex"])),
        "hr": [[r["t"], r["hr"]] for r in part if r["hr"] is not None],
    })
    if sess.get("steps") is None:
        sess["steps"] = sum(r["steps"] for r in part)
    sess["kcal_range"] = sci.kcal_range(sess["kcal"])
    return sess


def band_workouts(con, start, end):
    cols = columns(con, "BASE_ACTIVITY_SUMMARY")
    if not {"START_TIME", "END_TIME"} <= cols:
        return []
    # only the day's workouts and only the columns needed (the table also holds the raw data of each workout);
    # START_TIME in milliseconds (Gadgetbridge) or seconds
    dev = " AND DEVICE_ID=?" if "DEVICE_ID" in cols else ""
    name = '"NAME"' if "NAME" in cols else "NULL"
    rows = con.execute(f'SELECT START_TIME, END_TIME, {name} FROM "BASE_ACTIVITY_SUMMARY" WHERE '
                       f'((START_TIME>=? AND START_TIME<?) OR (START_TIME>=? AND START_TIME<?)){dev} ORDER BY rowid',
                       (start, end, start * 1000, end * 1000) + ((DEVICE_ID,) if dev else ())).fetchall()
    out = []
    for s, e, name_ in rows:
        if not s or not e:
            continue
        s = s // 1000 if s > 10**11 else s
        e = e // 1000 if e > 10**11 else e
        out.append({"start": int(s), "end": int(e), "source": "band", "steps": None,
                    "name": (name_ or "").strip() or M("kind.workout")})
    return out


# ---------------------------------------------------------------- sleep

SLEEP_RECORD = 594      # bytes of a sleep summary (type 0x48 SLEEP_RESULT)
SLEEP_MAX_STAGES = 50   # fixed slots for the stages of the main sleep and of the naps
SLEEP_MAX_NAPS = 10     # fixed slots for naps


def parse_sleep_blob(blob):
    """Decodes the sleep summary computed by the band (same data as the Zepp app).

    Format confirmed by the parser of the Zepp app 10.9.0 (static analysis, 12 October 2026), little-endian:
      0x00 u32 update time · 0x04 u32 midnight of the day · 0x08 u8 version · 0x09 main sleep present
      0x0A 4 x s16 in minutes from midnight of the previous day: sleep start, sleep end, in bed, out of bed
      0x13 naps supported · 0x14 naps present · 0x16 score
      0x17 s8 number of naps · 0x18 10 x (s16 start, s16 end, s16 duration in minutes)
      0x54 s8 main sleep stages · 0x55 s8 nap stages (separate byte: not to be read together with the previous one)
      0x56 50 stages of 5 bytes (s16 start, s16 inclusive end, u8 type) · 0x24A 4 x s16 totals (REM, light, deep, awake)
    With version 0 the end of the main sleep is moved by one minute, as the Zepp app does.
    """
    if not blob or len(blob) < SLEEP_RECORD:
        return None
    day0 = struct.unpack_from("<I", blob, 4)[0]
    version, has_main = blob[8], blob[9]
    if not has_main:
        return None
    n = struct.unpack_from("<b", blob, 0x54)[0]
    if n <= 0 or n > SLEEP_MAX_STAGES:
        return None
    # minutes from day0 - 24 h, as the Zepp app (wg50.f: day + (minutes - 1440)) and Gadgetbridge
    # (timestampMidnight - 24 h + minutes) count them: not from the local midnight of the day before, which is
    # an hour away from it after a 23- or 25-hour day (the night after a clock change)
    base = day0 - 86400  # elapsed: the band's own reference, 24 hours before day0
    sleep_s, sleep_e, bed_s, bed_e = struct.unpack_from("<4h", blob, 10)
    if version == 0:
        sleep_e += 1
    stages = []
    for k in range(n):
        s, e, t = struct.unpack_from("<hhB", blob, 0x56 + 5 * k)
        if s < 0 or e < s:
            return None
        stages.append((base + s * 60, base + (e + 1) * 60, SLEEP_STAGE.get(t, "light")))
    rem, light, deep, awake = struct.unpack_from("<4h", blob, 0x24A)
    naps = []
    if blob[0x13] and blob[0x14]:
        count = max(0, min(SLEEP_MAX_NAPS, struct.unpack_from("<b", blob, 0x17)[0]))
        for k in range(count):
            s, e, length = struct.unpack_from("<3h", blob, 0x18 + 6 * k)
            if s < 0 or e < s or length <= 0:
                continue
            naps.append({"start": base + s * 60, "end": base + (e + 1) * 60, "minutes": int(length)})
    return {"day0": day0, "stages": stages, "score": blob[22] or None,
            "start": base + sleep_s * 60, "end": base + sleep_e * 60,
            "bed_start": base + bed_s * 60, "bed_end": base + bed_e * 60,
            "totals": {"rem": rem, "light": light, "deep": deep, "awake": awake}, "naps": naps}


def sleep_from_band(con, d):
    if not {"TIMESTAMP", "DATA"} <= columns(con, "HUAMI_SLEEP_SESSION_SAMPLE"):
        return None
    day0, day1 = midnight(d), midnight(d + timedelta(days=1))
    mult = ts_mult(con, "HUAMI_SLEEP_SESSION_SAMPLE") or 1000
    # the band updates the summary several times: the last one of the day wins
    rows = con.execute(
        'SELECT DATA FROM "HUAMI_SLEEP_SESSION_SAMPLE" WHERE DEVICE_ID=? AND TIMESTAMP>=? AND TIMESTAMP<? '
        'ORDER BY TIMESTAMP DESC', (DEVICE_ID, day0 * mult, day1 * mult)).fetchall()
    for (blob,) in rows:
        parsed = parse_sleep_blob(blob)
        if parsed and parsed["day0"] == day0:
            return parsed
    return None


def sleep_from_samples(con, d):
    """Fallback without the band's summary: samples of kind 'sleep' from 18:00 of the day before to 12:00, stages
    as in Gadgetbridge."""
    rows = activity_rows(con, at(d - timedelta(days=1), 18), at(d, 12))

    def stage(r):
        if r.get("RAW_KIND") != KIND_SLEEP:
            return None
        if (r.get("REM_SLEEP", 0) & 127) > 55:
            return "rem"
        if (r.get("DEEP_SLEEP", 0) & 127) > 42:
            return "deep"
        return "light"

    marked = [(r["t"], stage(r)) for r in rows]
    idx = [i for i, (_, s) in enumerate(marked) if s]
    if not idx:
        return None
    blocks, cur = [], [idx[0]]
    for i in idx[1:]:
        if marked[i][0] - marked[cur[-1]][0] > 3600:
            blocks.append(cur)
            cur = [i]
        else:
            cur.append(i)
    blocks.append(cur)
    main = max(blocks, key=len)
    seg = marked[main[0]: main[-1] + 1]
    stages = [(t, t + 60, s or "awake") for t, s in seg]
    return {"stages": stages, "score": None, "start": seg[0][0], "end": seg[-1][0] + 60,
            "bed_start": seg[0][0], "bed_end": seg[-1][0] + 60, "totals": None}


_sleepcache = Memo(400)
SLEEP_TABLES = ("GENERIC_HRV_VALUE_SAMPLE", "HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE", "HUAMI_SPO2_SAMPLE",
                "GENERIC_TEMPERATURE_SAMPLE", ACT)


def sleep_ranges(d, res):
    """Rows the night ending on day d was computed from: the band's summaries of the day, the samples of the
    fallback window and, once the night is known, its heart rate, HRV, breathing, SpO2 and temperature."""
    out = [("HUAMI_SLEEP_SESSION_SAMPLE", midnight(d), midnight(d + timedelta(days=1))),
           (ACT, at(d - timedelta(days=1), 18), at(d, 12))]
    if res:
        out += [(t, res["start"], res["end"] + 60) for t in SLEEP_TABLES]
    return out


def sleep_for(con, d):
    """Sleep of the night ending on day d: a past night is computed again only if its rows change, last night
    (the band can still update it) once a minute."""
    if d >= now().date():
        return _sleepcache.get((DB, d.isoformat(), "today"), lambda: _sleep_for(con, d), version=now_version(con))
    return by_data(_sleepcache, (DB, d.isoformat()), con, lambda: _sleep_for(con, d), lambda r: sleep_ranges(d, r))


def _sleep_for(con, d):
    raw = sleep_from_band(con, d)
    source = "band"
    if raw is None:
        raw = sleep_from_samples(con, d)
        source = "samples"
    if raw is None:
        return None
    counts = {"deep": 0, "rem": 0, "light": 0, "awake": 0}
    timeline = []
    for a, b, s in raw["stages"]:
        counts[s] += round((b - a) / 60)
        if timeline and timeline[-1][2] == s and timeline[-1][1] == a:
            timeline[-1][1] = b
        else:
            timeline.append([a, b, s])
    total = counts["deep"] + counts["rem"] + counts["light"]
    if total == 0:
        return None
    start, end = raw["start"], raw["end"]
    in_bed = max(1, round((raw["bed_end"] - raw["bed_start"]) / 60))
    night = lambda tbl, col, lo, hi: [v for _, v in series(con, tbl, col, start, end + 60, lo, hi)]
    arows = activity_rows(con, start, end + 60)
    hr = [r["hr"] for r in arows if r["hr"] is not None]
    hrv = night("GENERIC_HRV_VALUE_SAMPLE", "VALUE", 5, 250)
    resp = night("HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE", "RATE", 4, 40)
    sp = spo2_clean(series(con, "HUAMI_SPO2_SAMPLE", "SPO2", start, end + 60, 70, 100), arows)
    temp = night("GENERIC_TEMPERATURE_SAMPLE", "TEMPERATURE", 20, 42)
    return {
        "source": source, "score": raw["score"],
        "total": total, "deep": counts["deep"], "rem": counts["rem"], "light": counts["light"],
        "awake": counts["awake"], "start": start, "end": end,
        "bed_start": raw["bed_start"], "bed_end": raw["bed_end"], "in_bed": in_bed,
        "latency": max(0, round((start - raw["bed_start"]) / 60)),
        "efficiency": min(100, round(100 * total / in_bed)),
        "avg_hr": rnd(mean(hr)), "min_hr": min(hr) if hr else None,
        "hrv": rnd(statistics.median(hrv)) if hrv else None,
        "resp": rnd(mean(resp), 1), "spo2": sp["avg"] if sp else None, "spo2_min": sp["min"] if sp else None,
        "spo2_min_raw": sp["min_raw"] if sp else None, "spo2_dips": sp["isolated_dips"] if sp else 0,
        "spo2_motion": sp["motion_removed"] if sp else 0,
        "hr_fixed": len(hr_fixes(arows)),
        "temp": rnd(mean(temp), 1),
        "awakenings": sum(1 for a, b, st in timeline[1:-1] if st == "awake"),
        "restful": counts["deep"] + counts["rem"],
        "timeline": timeline,
        # naps recorded by the band in the same summary: shown separately, outside duration and efficiency
        "naps": raw.get("naps") or [],
        "nap_min": sum(n["minutes"] for n in raw.get("naps") or []),
    }


# ---------------------------------------------------------------- workouts recognised from heart rate
# Gadgetbridge splits movement into step sessions: a hike with breaks becomes 11 "walks"
# and a strength workout with few steps can disappear. Here a workout is a continuous block of
# elevated heart rate; Gadgetbridge sessions remain as segments inside it.

TRAIN_HRR = 0.30        # threshold: resting HR + 30% of heart rate reserve (below the ACSM moderate intensity of 40%)
TRAIN_GAP_S = 180       # breaks up to 3 minutes do not interrupt the workout
TRAIN_MIN_S = 600       # minimum duration 10 minutes
TRAIN_HOT_FRAC = 0.6    # at least 60% of the minutes above threshold

# activity types (codes, kept in Italian because they are stored in the user's data); names in the catalogs (act.<code>)
ACTIVITY_TYPES = {k: (M(f"act.{k}"),) for k in ("walk", "hike", "run", "strength", "swim", "rope", "work", "other")}
# types where added load or a backpack make sense; for the others the value is ignored
LOAD_TYPES = ("walk", "hike", "strength")


def label_load(typ, load_kg):
    """Added load valid only for the types that use it: switching from strength (15 kg) to swimming drops it."""
    return float(load_kg or 0) if typ in LOAD_TYPES else 0


# words that, in a plan written as plain text, give the activity type (Italian and English)
PLAN_WORDS = [("forza", "strength"), ("strength", "strength"), ("corsa", "run"), ("corr", "run"), ("run", "run"),  # keywords
              ("nuoto", "swim"), ("swim", "swim"), ("corda", "rope"), ("rope", "rope"),  # keywords
              ("trekking", "hike"), ("escursion", "hike"), ("hik", "hike"),  # keywords
              ("camminata", "walk"), ("walk", "walk"), ("lavoro", "work"), ("work", "work")]  # keywords
REST_WORDS = ("riposo", "rest")  # keywords
_labels_lock = threading.Lock()


def state_path(name):
    return STATE_DIR / name


# Internal codes were Italian up to versionCode 259: data saved or exported by older versions (and the view saved by
# the Android app) can still contain them. They are converted when read; only the English codes are ever written.
LEGACY_CODES = {
    # activity types
    "camminata": "walk", "trekking": "hike", "corsa": "run", "forza": "strength", "nuoto": "swim", "corda": "rope",
    "lavoro": "work", "altro": "other",
    # meals, nutrition goals, food kinds
    "colazione": "breakfast", "pranzo": "lunch", "cena": "dinner", "spuntini": "snacks",
    "perdere": "lose_fat", "mantenere": "maintain", "alimento": "food", "ricetta": "recipe",
    # origins of a value
    "fascia": "band", "stima": "estimate", "manuale": "manual", "campioni": "samples", "osservata": "observed",
    "età": "age", "impostata": "set", "piano": "plan", "registro": "log",
    # statuses
    "raccolta": "collecting", "norma": "normal", "sopra": "above", "sotto": "below", "segui": "follow",
    "leggero": "light", "riposo": "rest", "certo": "certain", "probabile": "probable", "attenzione": "caution",
    "incompleto": "incomplete", "corso": "in_progress", "fatto": "done", "diverso": "different", "saltato": "skipped",
    "mancante": "missing", "oggi": "today",
    # load categories
    "moderato": "moderate", "alto": "high", "molto_alto": "very_high",
    # export kinds and tabs of the page
    "giorni": "days", "allenamenti": "workouts", "cibo": "food", "sonno": "sleep", "salute": "health",
    "profilo": "profile",
}
LEGACY_FORMATS = {"it": "eu"}  # CSV format: semicolon and decimal comma
# fields of the state files that hold codes (lists for "types")
CODE_FIELDS = ("type", "kind", "meal", "goal", "load_src", "source")


def code(v):
    """English code for a code read from data or a request (old Italian codes converted)."""
    return LEGACY_CODES.get(v, v) if isinstance(v, str) else v


def migrate_codes(obj):
    """Converts old codes in a state file, in place. Returns True if anything changed."""
    changed = False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in CODE_FIELDS and isinstance(v, str) and v in LEGACY_CODES:
                obj[k] = LEGACY_CODES[v]
                changed = True
            elif k == "types" and isinstance(v, list) and any(x in LEGACY_CODES for x in v if isinstance(x, str)):
                obj[k] = [code(x) for x in v]
                changed = True
            elif isinstance(v, (dict, list)):
                changed = migrate_codes(v) or changed
    elif isinstance(obj, list):
        for v in obj:
            changed = migrate_codes(v) or changed
    return changed


def state_load(name, default=None):
    try:
        data = json.loads(state_path(name).read_text())
    except (OSError, ValueError):
        return default if default is not None else {}
    if not isinstance(data, dict):
        return default if default is not None else {}
    if migrate_codes(data):
        try:
            state_save(name, data)  # from now on the file has only English codes
        except OSError:
            pass
    return data


def state_version(name):
    try:
        return state_path(name).stat().st_mtime_ns
    except OSError:
        return 0


_viewcache = {}
_viewlock = threading.Lock()


def state_view(name, load, extra=None):
    """load() of a state file for reading only (never change what it returns), read again only when the file
    changes (or extra, another file it depends on): the computations of a page read the profile, the weights and
    the labels hundreds of times."""
    try:
        st = state_path(name).stat()
        ver = (STATE_DIR, extra, st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        ver = (STATE_DIR, extra, None)
    with _viewlock:
        hit = _viewcache.get(name)
    if hit and hit[0] == ver:
        return hit[1]
    value = load()
    with _viewlock:
        _viewcache[name] = (ver, value)
    return value


def state_save(name, data):
    """Atomic write, forced to disk: a power cut does not leave the file empty or half written."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(name)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(STATE_DIR, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass


def state_delete(name):
    try:
        state_path(name).unlink()
    except FileNotFoundError:
        pass


def labels_file():
    return state_path("labels.json")


def labels_load():
    return state_load("labels.json")


def labels_view():
    return state_view("labels.json", labels_load)


def labels_version():
    return state_version("labels.json")


def labels_save(data):
    state_save("labels.json", data)


LOAD_MAX = 60  # added load or backpack: maximum kg


def valid_load(v):
    """Valid added load (0-60 kg) or None."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 0 <= v <= LOAD_MAX:
        return None
    return round(float(v), 2)


def plan_for(d):
    """Programme of day d from the weekly plan.

    Each day can be a text ("Run or skipping rope") or an object with
    text, types (activity types), day (strength day 1-3), load_kg (added load of the strength session)
    and exercises (list of exercises).
    """
    entry = state_view("plan.json", load_plan, (PLAN_FILE, plan_version())).get(str(d.isoweekday()), "") or ""
    if isinstance(entry, dict):
        text = str(entry.get("text", ""))
        raw = entry.get("types", [])
        types = [t for t in (raw if isinstance(raw, list) else []) if isinstance(t, str) and t in ACTIVITY_TYPES]
        day = entry.get("day") if entry.get("day") in (1, 2, 3) else None
        raw = entry.get("exercises", [])
        exercises = [x for x in (raw if isinstance(raw, list) else []) if isinstance(x, (list, tuple)) and len(x) >= 1][:20]
        return {"text": text, "types": types, "day": day if "strength" in types else None,
                "load_kg": valid_load(entry.get("load_kg")) if "strength" in types else None,
                "rest": not types and any(w in text.lower() for w in REST_WORDS),
                "exercises": [[str(x[0]), str(x[1]) if len(x) > 1 else ""] for x in exercises]}
    text = str(entry)
    low = text.lower()
    types = []
    for word, t in PLAN_WORDS:
        if word in low and t not in types:
            types.append(t)
    m = re.search(r"(?:giorno|day)\s*(\d)", low)
    return {"text": text, "types": types, "day": int(m.group(1)) if m and "strength" in types else None, "load_kg": None,
            "rest": any(w in low for w in REST_WORDS) and not types, "exercises": []}


def session_load(pl):
    """Default added load of a strength session: the plan's, otherwise the kg written in the name of an
    exercise (for example "Lunges with 15 kg"), otherwise none."""
    if not pl:
        return 0.0
    if pl.get("load_kg") is not None:
        return pl["load_kg"]
    kgs = [default_kg(n) for n, _ in pl.get("exercises") or [] if exercise_kind(n) != "skip"]
    return max(kgs, default=0.0)


def strength_day_load(day, plan=None):
    """Default added load for strength day 1-3 (from the weekly plan) or, without a day, from the day's plan."""
    pl = plan_for_strength_day(day) if day in (1, 2, 3) else None
    return session_load(pl or plan)


def hr_blocks(rows, rest, hrmax, gb):
    thr = rest + TRAIN_HRR * (hrmax - rest)
    hot = [r["t"] for r in rows if r["hr"] is not None and r["hr"] >= thr
           and r.get("RAW_KIND") not in (KIND_SLEEP, KIND_NOT_WORN, KIND_CHARGING)]
    blocks = []
    for t in hot:
        if blocks and t - blocks[-1][1] <= TRAIN_GAP_S:
            blocks[-1][1] = t
            blocks[-1][2] += 1
        else:
            blocks.append([t, t, 1])
    out = []
    for a, b, n in blocks:
        length = (b - a) // 60 + 1
        if (b - a) + 60 < TRAIN_MIN_S or n < TRAIN_HOT_FRAC * length:
            continue
        # overlapping Gadgetbridge sessions widen the block (start of the movement)
        for sgm in gb:
            if sgm["start"] <= b + 60 and sgm["end"] >= a:
                a, b = min(a, sgm["start"]), max(b, sgm["end"])
        if out and a <= out[-1][1] + TRAIN_GAP_S:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def train_features(part, rest, hrmax):
    hrs = [r["hr"] for r in part if r["hr"] is not None]
    moving = [r["steps"] for r in part if r["steps"] > 40]
    n = max(1, len(part))
    hrr = lambda v: (v - rest) / max(1, hrmax - rest)
    steps = sum(r["steps"] for r in part)
    return {
        "dur": n, "steps": steps, "spm": round(steps / n, 1),
        "cad": statistics.median(moving) if moving else 0,
        "move": round(len(moving) / n, 2),
        "hrr": round(hrr(mean(hrs)), 2) if hrs else 0,
        "hrr_max": round(hrr(max(hrs)), 2) if hrs else 0,
        "hr_sd": round(statistics.pstdev(hrs), 1) if len(hrs) > 1 else 0,
        "rope_min": len(rope_minutes(part)),
    }


ROPE_SPM = 90  # jumps counted as steps: at least 90 per minute, for at least 2 minutes in a row


def rope_minutes(part):
    """Skipping-rope minutes inside a session: high and sustained steps per minute.

    An isolated minute (standing up, fetching the weights) does not count: a neighbour above threshold is needed too.
    """
    hi = [r["steps"] >= ROPE_SPM for r in part]
    return {part[i]["t"] for i in range(len(part))
            if hi[i] and ((i > 0 and hi[i - 1]) or (i + 1 < len(hi) and hi[i + 1]))}


def rope_runs(part):
    ts = sorted(rope_minutes(part))
    runs = []
    for t in ts:
        if runs and t - runs[-1][1] <= 120:
            runs[-1][1] = t
        else:
            runs.append([t, t])
    return [[a, b + 60] for a, b in runs if b - a >= 60]


def rule_type(f, plan):
    """Rules on the minute-by-minute data. Returns (type, reason)."""
    pt = plan["types"]
    if f["cad"] >= 140 and f["move"] >= 0.5:
        return "run", M("why.run_cadence")
    if "rope" in pt and f["hrr"] >= 0.5 and f["dur"] <= 45 and f["cad"] >= 90 and mostly_rope(f):
        return "rope", M("why.rope_planned")
    if f["dur"] >= 90 and f["move"] >= 0.3 and f["hrr"] >= 0.35:
        return "hike", M("why.hike")
    if f["move"] >= 0.7 and f["hr_sd"] < 10 and f["hrr"] < 0.45:
        return "walk", M("why.walk_steady")
    if f["move"] >= 0.85 and f["cad"] < 140:
        return "walk", M("why.walk_steps")
    if f["spm"] < 15 and f["hrr"] >= 0.3:
        if "swim" in pt:
            return "swim", M("why.swim_planned")
        return "strength", M("why.hr_no_steps")
    if f["hr_sd"] >= 10 and f["dur"] < 90:
        return "strength", M("why.hr_waves")
    if f["move"] >= 0.5:
        return "walk", M("why.moving")
    return "other", M("why.unusual")


FEAT_KEYS = ["dur", "spm", "cad", "move", "hrr", "hr_sd"]
# scale of each feature: difference considered "one unit" (log of duration ≈ ±40%)
FEAT_SCALE = {"dur": 0.35, "spm": 15.0, "cad": 15.0, "move": 0.15, "hrr": 0.08, "hr_sd": 4.0}
KNN_MAX_DIST = 2.0


def knn_type(f, labels, k=3):
    """Comparison with the workouts you have already labelled (k most similar neighbours).

    Euclidean distance on fixed scales for each feature: a workout unlike all those
    already labelled is not forced into a category (mean distance above KNN_MAX_DIST).
    """
    pts = [(lb["type"], lb.get("day"), lb["features"]) for lb in labels.values()
           if lb.get("type") and lb.get("features")]
    if len(pts) < 3:
        return None

    def val(ft, key):
        v = ft.get(key, 0) or 0
        return math.log1p(v) if key == "dur" else v

    def dist(ft):
        return math.sqrt(sum(((val(ft, key) - val(f, key)) / FEAT_SCALE[key]) ** 2 for key in FEAT_KEYS))

    near = sorted(((dist(ft), t, dy) for t, dy, ft in pts), key=lambda x: x[0])[:k]
    votes = {}
    for dd, t, dy in near:
        votes[t] = votes.get(t, 0) + 1
    best = max(votes, key=votes.get)
    mean_d = mean([dd for dd, t, _ in near if t == best])
    if votes[best] < 2 or mean_d > KNN_MAX_DIST:
        return None
    days = [dy for _, t, dy in near if t == best and dy]
    return {"type": best, "day": max(set(days), key=days.count) if days else None, "distance": round(mean_d, 2),
            "votes": votes[best], "k": len(near)}


def suggest(f, plan, labels):
    t, first = rule_type(f, plan)
    why = [first]  # reasons, shown as a list
    conf = "high" if t in plan["types"] else "medium"
    nn = knn_type(f, labels)
    day = plan["day"] if t == "strength" else None
    planned = False
    if nn:
        if nn["type"] != t:
            why = [M("why.similar_as", type=M(f"act.{nn['type']}"))]
        else:
            why.append(M("why.similar"))
        t, conf = nn["type"], "high"
        day = nn["day"] if t == "strength" else None
        if t == "strength" and plan["day"]:
            day = plan["day"]
    elif conf == "medium" and t in ("other", "strength"):
        # the rules cannot tell clearly: the day's programme decides, but only if compatible with the data
        for pt in plan["types"]:
            if pt != t and compatible(pt, f):
                t, conf, why, planned = pt, "low", [M("why.planned", plan=plan["text"])], True
                day = plan["day"] if t == "strength" else None
                break
    if t == "strength" and f.get("rope_min", 0) >= 5:
        why.append(M("why.with_rope", min=f["rope_min"]))
    if plan["text"] and t in plan["types"] and not planned:
        why.append(M("why.planned", plan=plan["text"]))
    # when a strength session is confirmed the plan's added load applies (same weight for the whole session)
    load = strength_day_load(day, plan if "strength" in plan["types"] else None) if t == "strength" else 0.0
    return {"type": t, "day": day, "name": type_name(t, day), "confidence": conf, "reason": why, "load_kg": load}


def mostly_rope(f):
    """Rope-only session: rope minutes are most of it. Rope + sets = strength."""
    return "rope_min" not in f or f["rope_min"] >= 0.7 * f["dur"]


def compatible(t, f):
    """Is the planned type plausible with what the band measured?"""
    return {
        "swim": f["spm"] < 15,
        "rope": f["cad"] >= 80 and f["hrr"] >= 0.45 and f["dur"] <= 60 and mostly_rope(f),
        "run": f["cad"] >= 130,
        "walk": f["move"] >= 0.5,
        "hike": f["dur"] >= 60 and f["move"] >= 0.3,
        "strength": True, "work": f["hrr"] < 0.35, "other": True,
    }.get(t, False)


def type_name(t, day=None):
    if t == "strength" and day:
        return M("act.strength_day", n=day)
    return ACTIVITY_TYPES.get(t, (M("kind.activity"),))[0]


def match_label(labels, a, b):
    for key, lb in labels.items():
        la, lb_end = lb.get("start", 0), lb.get("end", 0)
        overlap = min(b, lb_end) - max(a, la)
        if abs(la - a) <= 300 or overlap > 0.5 * max(60, min(b - a, lb_end - la)):
            return key, lb
    return None, None


def pandolf_kcal_min(steps_min, p, load_kg, grade=0.0, eta=1.0):
    """Pandolf et al. 1977: cost of walking with a load (W) converted to kcal/min."""
    w, l = p["weight"], load_kg
    v = steps_min * p["stride_m"] / 60.0
    watts = 1.5 * w + 2.0 * (w + l) * (l / w) ** 2 + eta * (w + l) * (1.5 * v * v + 0.35 * v * grade)
    return watts * 0.01433


# Compendium 2024, resistance training: the code is chosen from the heart rate during the sets (fraction of heart
# rate reserve). METs are per kg of body weight: with weights on, the Compendium changes code, it does not add the load
# to the weight. Without added load the circuit training values apply.
STRENGTH_CODES = {3.5: ("02054", M("met.02054")), 5.0: ("02052", M("met.02052")), 7.5: ("02040", M("met.02040"))}

# Active work: a standing base chosen for each activity (or the profile's default), Compendium 2024, plus the steps
# recorded in it at the cost of walking above standing (Looney 2019); a minute with the heart rate above the threshold
# uses the heart rate method if that gives more. Standing costs little more than sitting (Saeidifard 2018: 0.15 kcal
# a minute): hours on one's feet are not a workout, and the walking in them is counted step by step, once.
WORK_LEVELS = {"still": (1.3, "07040"), "moving": (1.5, "07041"), "heavy": (3.8, "11620")}
WORK_DEFAULT = "moving"


def work_levels():
    """The intensities for the page: code, MET and Compendium code (the names and descriptions are in the catalogs)."""
    return [{"key": k, "met": met, "code": c} for k, (met, c) in WORK_LEVELS.items()]


def work_level(lb, p):
    """Intensity of a work activity: the one chosen for it, otherwise the profile's default."""
    v = (lb or {}).get("work_level")
    return v if v in WORK_LEVELS else p.get("work_level") if p.get("work_level") in WORK_LEVELS else WORK_DEFAULT


def work_kcal(part, p, hr_threshold, level):
    """Active calories of a work activity: standing base of the level plus the steps of each minute; heart rate where
    it is above the threshold and gives more. Returns (kcal, minutes counted with the heart rate)."""
    met = WORK_LEVELS[level][0]
    base = (met - 1.0) * p["weight"] / 60.0
    kcal, by_hr = 0.0, 0
    for r in part:
        m = base + sci.walk_kcal(r["steps"], p["stride_m"], p["weight"])
        if r["hr"] is not None and r["hr"] >= hr_threshold:
            hk = sci.hr_active_kcal(r["hr"], p.get("rest") or 60, p["hrmax"], p["vo2max"], p["bmr"], p["weight"])
            if hk > m:
                m, by_hr = hk, by_hr + 1
        kcal += m
    return kcal, by_hr


def typed_kcal(t, part, f, p, hr_threshold, load_kg=0.0, rest=None, level=None):
    """Active calories of a workout by type (confirmed label); level: intensity of a work activity."""
    base = sum(kcal_active_minute(r, p, hr_threshold) for r in part)
    hours = len(part) / 60.0
    met_net = lambda met: max(0.0, (met - 1.0) * p["weight"] * hours)
    if t == "strength":
        # rope inside the session (warm-up, finish) is rhythmic aerobic work: there heart rate estimates
        # consumption well; in strength sets instead heart rate overestimates it, hence MET values.
        rope = rope_minutes(part)
        strength = [r for r in part if r["t"] not in rope]
        hrs = [r["hr"] for r in strength if r["hr"] is not None]
        hrr = (mean(hrs) - rest) / max(1, p["hrmax"] - rest) if hrs and rest else f["hrr"]
        met = 3.5 if hrr < 0.35 else 5.0 if hrr < 0.55 else 7.5
        kcal = max(0.0, (met - 1.0) * p["weight"] * len(strength) / 60.0)
        if load_kg:
            code, desc = STRENGTH_CODES[met]
            what = M("kcal.strength_load", met=met, code=code, desc=desc, kg=load_kg)
        else:
            what = M("kcal.strength", met=met)
        if not rope:
            return kcal, what
        kcal += sum(kcal_active_minute(r, p, hr_threshold) for r in part if r["t"] in rope)
        return kcal, M("kcal.strength_rope", what=what, strength=len(strength), rope=len(rope))
    if t == "swim":
        met = 5.8 if f["hrr"] < 0.55 else 9.8
        return met_net(met), M("kcal.swim", met=met)
    if t == "work":
        level = level if level in WORK_LEVELS else WORK_DEFAULT
        kcal, by_hr = work_kcal(part, p, hr_threshold, level)
        met, code_ = WORK_LEVELS[level]
        return kcal, M("kcal.work", level=M(f"work.{level}"), met=met, code=code_, min=len(part),
                       steps=sum(r["steps"] for r in part), hr_min=by_hr)
    if t in ("walk", "hike") and load_kg:
        bmr_min = p["bmr"] / 1440
        eta = 1.2 if t == "hike" else 1.0
        pand = sum(max(0.0, pandolf_kcal_min(r["steps"], p, load_kg, eta=eta) - bmr_min) for r in part)
        if t == "hike":  # the slope is unknown: heart rate captures it better, Pandolf is the floor
            return max(base, pand), M("kcal.hike_load", kg=load_kg)
        return pand, M("kcal.walk_load", kg=load_kg)
    return base, DEFAULT_KCAL_METHOD


def kcal_err(t, load_kg):
    """Margin of the plausible range: ±25% for heart rate, steps and METs; Pandolf only upwards (Drain 2017)."""
    if t == "walk" and load_kg:
        return sci.PANDOLF_UNDER, True
    return sci.ACTIVE_ERR, False


def jump_minutes(t_type, part):
    """Skipping-rope minutes: all minutes with "steps" in a rope session, the rope minutes
    recognised inside a strength session. Jumps are not steps and cover no distance."""
    if t_type == "rope":
        return {r["t"] for r in part if r["steps"] > 0}
    if t_type == "strength":
        return rope_minutes(part)
    return set()


def training_entry(a, b, part, p, rest, hr_threshold, gb, f, sug, lb, source):
    hrs = [r["hr"] for r in part if r["hr"] is not None]
    zones = [0] * 5
    for hv in hrs:
        z = zone_of(hv, p["hrmax"])
        if z >= 0:
            zones[z] += 1
    kcal_default = sum(kcal_active_minute(r, p, hr_threshold) for r in part)
    load = 0
    if lb and lb.get("type"):
        load = label_load(lb["type"], lb.get("load_kg"))
        kcal, method = typed_kcal(lb["type"], part, f, p, hr_threshold, load, rest, work_level(lb, p))
        name = type_name(lb["type"], lb.get("day"))
    else:
        kcal, method, name = kcal_default, DEFAULT_KCAL_METHOD, sug["name"]
    t_type = lb["type"] if lb and lb.get("type") else sug["type"]
    # jumps are removed from steps only in confirmed workouts: a wrong suggestion
    # (a walk taken for strength) must not make real steps disappear
    jm = jump_minutes(lb["type"] if lb and lb.get("type") else None, part)
    jumps = sum(r["steps"] for r in part if r["t"] in jm)
    err, up = kcal_err(t_type, load)
    return {
        "start": a, "end": b, "duration_min": round((b - a) / 60) + (0 if source == "manual" else 1), "steps": f["steps"],
        "jumps": jumps,
        "distance_km": round((f["steps"] - jumps) * p["stride_m"] / 1000, 2),
        "avg_hr": round(mean(hrs)) if hrs else None, "max_hr": max(hrs) if hrs else None,
        "zones": zones, "trimp": round(sci.banister_trimp(hrs, rest, p["hrmax"], p["sex"])),
        "hr": [[r["t"], r["hr"]] for r in part if r["hr"] is not None],
        "features": f, "suggestion": sug, "source": source,
        "label": ({"type": lb["type"], "day": lb.get("day") if lb["type"] == "strength" else None,
                   "load_kg": label_load(lb["type"], lb.get("load_kg")),
                   "work_level": lb.get("work_level") if lb["type"] == "work" and lb.get("work_level") in WORK_LEVELS
                   else None,
                   "work_level_used": work_level(lb, p) if lb["type"] == "work" else None} if lb else None),
        "name": name, "kcal": round(kcal), "kcal_default": round(kcal_default), "kcal_method": method,
        "kcal_range": sci.kcal_range(kcal, err, up),
        "segments": [x["start"] for x in gb if x["source"] == "gadgetbridge" and a <= x["start"] <= b],
        "rope": rope_runs(part),
    }


def build_trainings(d, rows, p, rest, hr_threshold, gb):
    labels = labels_view()
    auto_labels = {k: v for k, v in labels.items() if not v.get("manual")}
    day_a, day_b = midnight(d), midnight(d + timedelta(days=1))
    manual = [lb for lb in labels.values() if lb.get("manual") and lb.get("type") and day_a <= lb["start"] < day_b]
    plan = plan_for(d)
    out = []
    # every minute belongs to one activity at most, so its calories and steps count once: first the activities
    # entered by hand (in order of time), then the heart rate blocks without their minutes
    taken = set()
    for m in sorted(manual, key=lambda x: x["start"]):
        part = [r for r in rows if m["start"] <= r["t"] < m["end"] and r["t"] not in taken]
        taken.update(r["t"] for r in part)
        f = train_features(part, rest, p["hrmax"])
        sug = {"type": m["type"], "day": m.get("day"), "name": type_name(m["type"], m.get("day")),
               "confidence": "manual", "reason": [M("why.manual")]}
        out.append(training_entry(m["start"], m["end"], part, p, rest, hr_threshold, gb, f, sug, m, "manual"))
    for a, b in hr_blocks(rows, rest, p["hrmax"], [x for x in gb if x["source"] == "gadgetbridge"]):
        # a block inside a manually added activity (for example a work shift) belongs to it
        if any(min(b, m["end"]) - max(a, m["start"]) > 0.5 * (b - a + 60) for m in manual):
            continue
        part = [r for r in rows if a <= r["t"] <= b and r["t"] not in taken]
        if not part:
            continue
        a, b = part[0]["t"], part[-1]["t"]  # without the edges shared with an activity entered by hand
        f = train_features(part, rest, p["hrmax"])
        _, lb = match_label(auto_labels, a, b)
        out.append(training_entry(a, b, part, p, rest, hr_threshold, gb, f, suggest(f, plan, labels), lb, "battito"))
    return sorted(out, key=lambda x: x["start"])


def save_label(start, end, typ, day, load_kg, manual=False, level=None):
    """Saves (or deletes with typ=None) the label of a workout, with its features for the kNN.

    manual=True: activity entered by hand with its times (for example a work shift without high heart rate).
    level: intensity of a work activity (WORK_LEVELS); None follows the profile's default.
    """
    typ = code(typ)
    if typ is not None and typ not in ACTIVITY_TYPES:
        raise UserError("err.type_invalid")
    if level is not None and level not in WORK_LEVELS:
        raise UserError("err.work_level_invalid")
    if day is not None and (isinstance(day, bool) or day not in (1, 2, 3)):
        raise UserError("err.day_invalid")
    if load_kg is not None and not (0 <= load_kg <= 60):
        raise UserError("err.load_invalid")
    if not (0 < start < end <= start + 12 * 3600):  # elapsed: at most 12 hours
        raise UserError("err.interval_invalid")
    if manual and start > now().timestamp() + 3600:
        raise UserError("err.future_activity")
    d = datetime.fromtimestamp(start, TZ).date()
    with closing(connect()) as con:
        p = profile(con, d)
        rows = activity_rows(con, start - 60, end + 60)
        rs = series(con, "HUAMI_HEART_RATE_RESTING_SAMPLE", "HEART_RATE", midnight(d), midnight(d + timedelta(days=1)), HR_MIN, HR_MAX)
        hrs = sorted(r["hr"] for r in rows if r["hr"] is not None)
    rest = rs[-1][1] if rs else (round(mean(hrs[:10])) if len(hrs) >= 10 else 60)
    part = [r for r in rows if start <= r["t"] <= end]
    with _labels_lock:
        labels = labels_load()
        if manual:
            key = next((k for k, v in labels.items() if v.get("manual") and v["start"] == start), None)
        else:
            key, _ = match_label({k: v for k, v in labels.items() if not v.get("manual")}, start, end)
        if typ is None:
            if key:
                labels.pop(key)
        else:
            entry = {"start": start, "end": end, "type": typ, "day": day if typ == "strength" else None,
                     "load_kg": label_load(typ, load_kg), "features": train_features(part, rest, p["hrmax"]),
                     "saved": int(time.time())}
            if typ == "work" and level:
                entry["work_level"] = level
            if manual:
                entry["manual"] = True
            labels[key or (f"m{start}" if manual else str(start))] = entry
        labels_save(labels)
        if typ == "strength" and load_kg is not None:
            sync_strength_load(d, label_load(typ, load_kg))
    _daycache.clear()
    return {"ok": True}


def sync_strength_load(d, load):
    """There is one added load per session: if the day's strength log uses the session load,
    it is updated with the confirmed workout's one (call it with the lock held)."""
    data = strength_load()
    rec = data.get(d.isoformat())
    if not rec or "load_kg" not in rec or rec["load_kg"] == load:
        return
    rec["load_kg"] = load
    for e in rec["entries"]:
        for st in e["sets"]:
            st["kg"] = load
    state_save("strength.json", data)


# ---------------------------------------------------------------- day

_daycache = Memo(800)


def compute_day(con, d, p):
    """The day's activity, workouts and calories. A past day is computed again only if its rows change (a sync of
    today leaves it as it is), today once a minute; labels, plan and profile are part of the key."""
    key = (DB, labels_version(), plan_version(), PLAN_FILE, d.isoformat(), json.dumps(p, sort_keys=True))
    if d >= now().date():
        return _daycache.get(key + ("today",), lambda: _compute_day(con, d, p), version=now_version(con))
    a, b = midnight(d), midnight(d + timedelta(days=1))
    return by_data(_daycache, key, con, lambda: _compute_day(con, d, p),
                   lambda r: [(ACT, a, b), ("HUAMI_HEART_RATE_RESTING_SAMPLE", a, b), ("BASE_ACTIVITY_SUMMARY", a, b)])


def _compute_day(con, d, p):
    today = now().date()
    start = midnight(d)
    end = midnight(d + timedelta(days=1))
    rows = activity_rows(con, start, end)
    worn = [r for r in rows if r.get("RAW_KIND") not in (KIND_NOT_WORN, KIND_CHARGING)]
    hrs = [r["hr"] for r in rows if r["hr"] is not None]

    rs = series(con, "HUAMI_HEART_RATE_RESTING_SAMPLE", "HEART_RATE", start, end, HR_MIN, HR_MAX)
    if rs:
        resting, resting_src = rs[-1][1], "band"
    elif len(hrs) >= 10:
        resting, resting_src = round(mean(sorted(hrs)[:10])), "estimate"
    else:
        resting, resting_src = None, None

    rest_ref = resting or p.get("rest_hr") or 60
    p = {**p, "rest": rest_ref}
    hr_threshold = rest_ref + 0.4 * (p["hrmax"] - rest_ref)
    zones = [0] * 5
    for h in hrs:
        z = zone_of(h, p["hrmax"])
        if z >= 0:
            zones[z] += 1

    active_kcal = sum(kcal_active_minute(r, p, hr_threshold) for r in worn)
    # basal metabolism of every hour of the day (23 or 25 on the days the clock changes): with the activity so far,
    # today's total is the least the day will cost (see day_energy)
    bmr_kcal = p["bmr"] * (end - start) / 86400  # elapsed: the basal metabolism is per 24 hours

    sessions = [describe_session(s, rows, p, hr_threshold)
                for s in sorted(band_workouts(con, start, end) + gb_sessions(rows, p["stride_m"]),
                                key=lambda s: s["start"])]
    gb = [s for s in sessions if s["source"] == "gadgetbridge"]
    trainings = build_trainings(d, rows, p, rest_ref, hr_threshold, sessions)
    active_kcal += sum(t["kcal"] - t["kcal_default"] for t in trainings)

    # skipping-rope jumps: left out of steps and distance (they stay in the Gadgetbridge data)
    jumped = set()
    for t in trainings:
        part = [r for r in rows if t["start"] <= r["t"] <= t["end"]]
        jumped |= jump_minutes((t.get("label") or {}).get("type"), part)
    for r in rows:
        r["jump"] = r["t"] in jumped
    steps_all = sum(r["steps"] for r in rows)
    jumps = sum(r["steps"] for r in rows if r["jump"])
    steps = steps_all - jumps

    # plausible range: basal metabolism ±10% (Madden 2016), active calories by the method of each part
    in_tr = sum(t["kcal"] for t in trainings)
    other = max(0.0, active_kcal - in_tr)
    low = bmr_kcal * (1 - sci.BMR_ERR) + other * (1 - sci.ACTIVE_ERR) + sum(t["kcal_range"][0] for t in trainings)
    high = bmr_kcal * (1 + sci.BMR_ERR) + other * (1 + sci.ACTIVE_ERR) + sum(t["kcal_range"][1] for t in trainings)

    res = {
        "date": d.isoformat(),
        "has_data": bool(hrs) or steps_all > 0,
        "steps": steps, "steps_band": steps_all, "jumps": jumps,
        "distance_km": round(steps * p["stride_m"] / 1000, 2),
        "active_min": round(sum(s["end"] - s["start"] for s in gb) / 60),
        "active_steps": sum(s["steps"] for s in gb),
        "kcal": {"total": round(bmr_kcal) + round(active_kcal), "active": round(active_kcal), "bmr": round(bmr_kcal),
                 "partial": d >= today,
                 "range": [round(low), round(high)],
                 "active_range": [round(other * (1 - sci.ACTIVE_ERR) + sum(t["kcal_range"][0] for t in trainings)),
                                  round(other * (1 + sci.ACTIVE_ERR) + sum(t["kcal_range"][1] for t in trainings))]},
        "zones": zones,
        "zone_minutes": zones[1] + zones[2] + 2 * (zones[3] + zones[4]),
        "trimp": sum(t["trimp"] for t in trainings),
        "resting": resting, "resting_src": resting_src,
        "hrs": hrs, "rows": rows, "sessions": sessions, "trainings": trainings,
        "start": start, "seconds": end - start, "profile": p,
        "worn_min": len(worn),  # minutes with the band on the wrist (needed to use the expenditure in the balance)
    }
    return res


def history(con, d, n):
    """Last n days up to and including d, each with its own profile (weight can change over time)."""
    return [compute_day(con, x, profile(con, x)) for x in (d - timedelta(days=i) for i in range(n - 1, -1, -1))]


def load_status(days28):
    """Load (Banister's TRIMP): last 7 days and weekly average of the last 4 weeks.

    No judgement on the acute/chronic ratio: Impellizzeri 2021 shows it does not predict injuries
    better than chance and recommends dropping it. The numbers stay to follow the trend.
    """
    data = [x for x in days28 if x["has_data"]]
    series_ = [[x["date"], x["trimp"] if x["has_data"] else None] for x in days28]
    week = sum(x["trimp"] for x in days28[-7:] if x["has_data"])
    if len(data) < 14:
        return {"week": week, "avg_week": None, "days": len(data), "series": series_}
    avg_week = sum(x["trimp"] for x in data) / len(data) * 7
    return {"week": week, "avg_week": round(avg_week), "days": len(data), "series": series_}


def baselines(con, d):
    """Resting heart rate (band) and overnight HRV of the last 60 days compared with the personal normal range."""
    days = [d - timedelta(days=i) for i in range(BASE_DAYS - 1, -1, -1)]
    rest = []
    for x in days:
        rs = series(con, "HUAMI_HEART_RATE_RESTING_SAMPLE", "HEART_RATE", midnight(x), midnight(x + timedelta(days=1)),
                    HR_MIN, HR_MAX)
        rest.append(rs[-1][1] if rs else None)
    hrv = []
    for x in days:
        sl = sleep_for(con, x)
        hrv.append(sl["hrv"] if sl and sl.get("hrv") else None)
    return {"rhr": baseline(rest, floor=1.0), "hrv": baseline(hrv, log=True, floor=0.02)}


def readiness(day, prev14, sleep, load, p, base=None):
    # if the band provides the resting heart rate only its values are used: estimates from the lowest
    # heart rates (for example on the first day, worn from midday) are not comparable
    band = [x["resting"] for x in prev14 if x["resting"] and x.get("resting_src") == "band"]
    base_vals = band if band or day.get("resting_src") == "band" else [x["resting"] for x in prev14 if x["resting"]]
    if len(base_vals) < 3 or not day["resting"]:
        return None
    score, parts = 75.0, []
    rb = base["rhr"] if base else None
    if rb and rb["mean"] is not None:
        # personal normal range (AIS): what counts is how far today is outside the band, not the raw difference
        diff = day["resting"] - rb["high"] if day["resting"] > rb["high"] else day["resting"] - rb["low"] if day["resting"] < rb["low"] else 0
        if diff > 0:
            score -= min(30, diff * 6)
            parts.append(M("ready.rhr_above", v=day["resting"], lo=rb["low"], hi=rb["high"]))
        else:
            score += 4
            parts.append(M("ready.rhr_in_band", lo=rb["low"], hi=rb["high"]))
    else:
        med = statistics.median(base_vals)
        diff = day["resting"] - med
        if diff > 0:
            score -= min(30, diff * 6)
            parts.append(M("ready.rhr_vs_avg", diff=round(diff)))
        else:
            score += min(8, -diff * 2)
            parts.append(M("ready.rhr_ok"))
    hb = base["hrv"] if base else None
    if hb and hb["mean"] is not None and hb["avg7"] is not None:
        # HRV-guided training (Vesterinen 2016): 7-day average below the normal range = reduce intensity
        if hb["status"] == "below":
            score -= 15
            parts.append(M("ready.hrv_low"))
        else:
            score += 5
            parts.append(M("ready.hrv_ok"))
    if sleep:
        ratio = sleep["total"] / p["sleep_goal_min"]
        score += max(-25, min(10, (ratio - 0.9) * 40))
        parts.append(M("ready.sleep", min=sleep["total"]))
    score = int(max(1, min(100, round(score))))
    level = "high" if score >= 80 else "good" if score >= 65 else "moderate" if score >= 45 else "low"
    return {"score": score, "level": level, "label": M(f"ready.level.{level}"), "baseline": round(statistics.median(base_vals)), "parts": parts}


def pai_for(con, d):
    cols = columns(con, "HUAMI_PAI_SAMPLE")
    if not {"TIMESTAMP", "PAI_TODAY", "PAI_TOTAL"} <= cols:
        return None
    mult = ts_mult(con, "HUAMI_PAI_SAMPLE") or 1000
    row = rows_dicts(con, 'SELECT * FROM "HUAMI_PAI_SAMPLE" WHERE DEVICE_ID=? AND TIMESTAMP>=? AND TIMESTAMP<? '
                          'ORDER BY TIMESTAMP DESC LIMIT 1',
                     (DEVICE_ID, midnight(d) * mult, midnight(d + timedelta(days=1)) * mult))
    if not row:
        return None
    r = row[0]
    return {"today": rnd(r.get("PAI_TODAY")), "week": rnd(r.get("PAI_TOTAL")),
            "low": rnd(r.get("PAI_LOW")), "moderate": rnd(r.get("PAI_MODERATE")), "high": rnd(r.get("PAI_HIGH")),
            "min_low": r.get("TIME_LOW"), "min_moderate": r.get("TIME_MODERATE"), "min_high": r.get("TIME_HIGH")}


def stress_summary(vals):
    v = [x for _, x in vals]
    if not v:
        return None
    share = {}
    for name, lo, hi in STRESS_BANDS:
        share[name] = round(100 * sum(1 for x in v if lo <= x <= hi) / len(v))
    return {"avg": round(mean(v)), "last": v[-1], "max": max(v), "share": share, "series": vals}


# ---------------------------------------------------------------- health assessment
# Thresholds taken from guidelines and meta-analyses. They are associations in large populations,
# not diagnoses: they help you see where you stand, they do not replace a doctor.

# Every source states study type, funding and conflicts of interest (checked on 9 October 2026).
# coi: "none" = declared absent or public funding only; "not_declared" = no statement in the text
# (old articles or unreadable page); "present" = ties with companies: the source is replaced and stays
# only if there is no independent alternative (flagged, with the reason in the catalogs, src.<id>.why).
SOURCES = {
    "paluch": {"title": "Paluch et al. 2022, Lancet Public Health",
              "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9289978/",
              "kind": "meta_analysis", "coi": "none"},
    "who": {"title": "Bull et al. 2020, Br J Sports Med",
           "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7719906/",
           "kind": "guideline", "coi": "none"},
    "momma": {"title": "Momma et al. 2022, Br J Sports Med",
             "url": "https://bjsm.bmj.com/content/56/13/755",
             "kind": "meta_analysis", "coi": "none"},
    "zhang": {"title": "Zhang et al. 2016, CMAJ",
             "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC4754196/",
             "kind": "meta_analysis", "coi": "none"},
    "tanaka": {"title": "Tanaka et al. 2001, JACC",
              "url": "https://pubmed.ncbi.nlm.nih.gov/11153730/",
              "kind": "meta_analysis", "coi": "none"},
    "mohoric": {"title": "Mohorič et al. 2022, IJERPH",
               "url": "https://www.mdpi.com/1660-4601/19/6/3535",
               "kind": "validation", "coi": "none"},
    "madden": {"title": "Madden et al. 2016, J Hum Nutr Diet",
              "url": "https://uhra.herts.ac.uk/id/eprint/5072/",
              "kind": "systematic_review", "coi": "none"},
    "ferri_marini": {"title": "Ferri Marini et al. 2021, Med Sci Sports Exerc",
                    "url": "https://doi.org/10.1249/MSS.0000000000002434",
                    "kind": "validation", "coi": "none"},
    "weir": {"title": "Weir 1949, J Physiol",
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC1392602/",
            "kind": "method", "coi": "not_declared"},
    "looney": {"title": "Looney et al. 2019, Med Sci Sports Exerc",
              "url": "https://doi.org/10.1249/MSS.0000000000001779",
              "kind": "meta_regression", "coi": "not_declared"},
    "pandolf": {"title": "Pandolf et al. 1977, J Appl Physiol",
               "url": "https://doi.org/10.1152/jappl.1977.43.4.577",
               "kind": "method", "coi": "not_declared"},
    "drain": {"title": "Drain et al. 2017, J Sci Med Sport",
             "url": "https://deakin.figshare.com/articles/journal_contribution/The_Pandolf_equation_under-predicts_the_metabolic_rate_of_contemporary_military_load_carriage/20831926",
             "kind": "validation", "coi": "not_declared"},
    "saeidifard": {"title": "Saeidifard et al. 2018, Eur J Prev Cardiol",
                   "url": "https://doi.org/10.1177/2047487317752186",
                   "kind": "meta_analysis", "coi": "not_declared"},
    "compendium": {"title": "Herrmann et al. 2024, J Sport Health Sci",
                  "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10818145/",
                  "kind": "compendium", "coi": "not_declared"},
    "fuller": {"title": "Fuller et al. 2020, JMIR mHealth",
              "url": "https://mhealth.jmir.org/2020/9/e18694",
              "kind": "systematic_review", "coi": "none"},
    "odriscoll": {"title": "O'Driscoll et al. 2020, Br J Sports Med",
                 "url": "https://bjsm.bmj.com/content/54/6/332",
                 "kind": "meta_analysis", "coi": "none"},
    "bent": {"title": "Bent et al. 2020, npj Digital Medicine",
            "url": "https://www.nature.com/articles/s41746-020-0226-6",
            "kind": "validation", "coi": "not_declared"},
    "walzel": {"title": "Walzel et al. 2023, Sensors",
              "url": "https://www.mdpi.com/1424-8220/23/22/9164",
              "kind": "validation", "coi": "none"},
    "aasm": {"title": "American Academy of Sleep Medicine, Scoring Manual FAQ",
            "url": "https://aasm.org/scoring-manual-faq-scoring-hypopneas",
            "kind": "definition", "coi": "not_declared"},
    "ais_hrv": {"title": "Australian Institute of Sport 2023",
               "url": "https://www.ais.gov.au/__data/assets/pdf_file/0013/1130260/HRV-BPG_FINAL.pdf",
               "kind": "guideline", "coi": "none"},
    "vesterinen": {"title": "Vesterinen et al. 2016, Med Sci Sports Exerc",
                  "url": "https://jyx.jyu.fi/bitstream/handle/123456789/50625/vesterinenetalindividualendurancemsse.pdf?sequence=1",
                  "kind": "rct", "coi": "none"},
    "granero": {"title": "Granero-Gallegos et al. 2020, IJERPH",
               "url": "https://www.mdpi.com/1660-4601/17/21/7999",
               "kind": "meta_analysis", "coi": "none"},
    "bittencourt": {"title": "Bittencourt et al. 2024, Front Physiol",
                   "url": "https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2024.1472702/full",
                   "kind": "rct", "coi": "none"},
    "uth": {"title": "Uth et al. 2004, Eur J Appl Physiol",
           "url": "https://link.springer.com/article/10.1007/s00421-003-0988-y",
           "kind": "validation", "coi": "not_declared"},
    "esco": {"title": "Esco et al. 2012, J Strength Cond Res",
            "url": "https://doi.org/10.1519/JSC.0b013e318238e863",
            "kind": "validation", "coi": "not_declared"},
    "han": {"title": "Han et al. 2022, Br J Sports Med",
           "url": "https://bjsm.bmj.com/content/56/13/733",
           "kind": "meta_analysis", "coi": "none"},
    "wehrlin": {"title": "Wehrlin & Hallén 2006, Eur J Appl Physiol",
               "url": "https://link.springer.com/article/10.1007/s00421-005-0081-9",
               "kind": "experiment", "coi": "not_declared"},
    "reynolds": {"title": "Reynolds et al. 2006, J Strength Cond Res",
                "url": "https://www.unm.edu/~rrobergs/478RMStrengthPrediction.pdf",
                "kind": "validation", "coi": "not_declared"},
    "morton": {"title": "Morton, Fitz-Clarke, Banister 1990, J Appl Physiol",
              "url": "https://doi.org/10.1152/jappl.1990.69.3.1171",
              "kind": "method", "coi": "not_declared"},
    "impellizzeri": {"title": "Impellizzeri et al. 2021, Sports Med",
                    "url": "https://iris.univr.it/retrieve/e34cfb98-e922-4c2f-aab5-18583ab7e31b/Impellizzeri_What%20Role%20Do%20Chronic%20Workloads%20Play_SportMed_2021.pdf",
                    "kind": "study", "coi": "none"},
    "cappuccio": {"title": "Cappuccio et al. 2010, Sleep",
                 "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC2864873/",
                 "kind": "meta_analysis", "coi": "none"},
    "chaput": {"title": "Chaput et al. 2020, Appl Physiol Nutr Metab",
              "url": "https://doi.org/10.1139/apnm-2020-0032",
              "kind": "systematic_review", "coi": "not_declared"},
    "fischer": {"title": "Fischer, Klerman, Phillips 2021, Sleep",
               "url": "https://doi.org/10.1093/sleep/zsab103",
               "kind": "method", "coi": "not_declared"},
    "wear": {"title": "Schyvens et al. 2024, JMIR mHealth",
            "url": "https://mhealth.jmir.org/2024/1/e52192",
            "kind": "systematic_review", "coi": "none"},
    "concheiro": {"title": "Concheiro-Moscoso et al. 2023, JMIR",
                 "url": "https://www.jmir.org/2023/1/e42073",
                 "kind": "validation", "coi": "none"},
    "nsf_q": {"title": "Ohayon et al. 2017, Sleep Health",
             "url": "https://escholarship.org/content/qt9xc5x5h2/qt9xc5x5h2.pdf",
             "kind": "consensus", "coi": "present", "flagged": True},
    "ohayon": {"title": "Ohayon et al. 2004, Sleep",
              "url": "https://www.med.upenn.edu/cbti/assets/user-content/documents/carskadonmeta-analysisresleepnorms.pdf",
              "kind": "meta_analysis", "coi": "present", "flagged": True},
    "pai": {"title": "Nes et al. 2017, Am J Med",
           "url": "https://www.em-consulte.com/article/1115189/personalized-activity-intelligence-pai-for-prevent",
           "kind": "cohort", "coi": "present", "flagged": True},
    "eu_kcal": {"title": "Regulation (EU) 1169/2011, Annex XIV",
               "url": "https://eur-lex.europa.eu/eli/reg/2011/1169/oj",
               "kind": "definition", "coi": "none"},
    "eu_tol": {"title": "European Commission 2012, Guidance document on tolerances",
              "url": "https://assets.publishing.service.gov.uk/media/5a7cc3eced915d63cc65cb79/EU-Guidance-on-Tolerance.pdf",
              "kind": "definition", "coi": "none"},
    "murphy_koehler": {"title": "Murphy & Koehler 2022, Scand J Med Sci Sports",
                      "url": "https://doi.org/10.1111/sms.14075",
                      "kind": "meta_analysis", "coi": "not_declared"},
    "hall": {"title": "Hall 2008, Int J Obes",
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC2376744/",
            "kind": "method", "coi": "not_declared"},
    "efsa_protein": {"title": "EFSA 2012",
                    "url": "https://doi.org/10.2903/j.efsa.2012.2557",
                    "kind": "guideline", "coi": "none"},
    "efsa_cho": {"title": "EFSA 2010",
                "url": "https://doi.org/10.2903/j.efsa.2010.1462",
                "kind": "guideline", "coi": "none"},
    "efsa_fat": {"title": "EFSA 2010",
                "url": "https://doi.org/10.2903/j.efsa.2010.1461",
                "kind": "guideline", "coi": "none"},
    "who_fat": {"title": "WHO 2023",
               "url": "https://www.who.int/publications/i/item/9789240073654",
               "kind": "guideline", "coi": "none"},
    "who_sfa": {"title": "WHO 2023",
               "url": "https://www.who.int/publications/i/item/9789240073630",
               "kind": "guideline", "coi": "none"},
    "who_cho": {"title": "WHO 2023",
               "url": "https://www.who.int/publications/i/item/9789240073593",
               "kind": "guideline", "coi": "none"},
    "who_salt": {"title": "WHO 2012",
                "url": "https://apps.who.int/iris/handle/10665/77985",
                "kind": "guideline", "coi": "none"},
    "craven": {"title": "Craven et al. 2021, Sports Med Open",
              "url": "https://sportsmedicine-open.springeropen.com/articles/10.1186/s40798-020-00297-0",
              "kind": "meta_analysis", "coi": "none"},
    "refalo": {"title": "Refalo, Trexler, Helms 2025, Strength Cond J",
              "url": "https://openrepository.aut.ac.nz/items/1aa54baf-6885-4739-aba7-df0087c3eae8",
              "kind": "meta_regression", "coi": "present", "flagged": True},
    "morton_prot": {"title": "Morton et al. 2018, Br J Sports Med",
                   "url": "https://bjsm.bmj.com/content/52/6/376",
                   "kind": "meta_analysis", "coi": "present", "flagged": True},
    "acsm_nutr": {"title": "Thomas, Erdman, Burke 2016, Med Sci Sports Exerc",
                 "url": "https://doi.org/10.1249/MSS.0000000000000852",
                 "kind": "consensus", "coi": "present", "flagged": True},
}
# removed sources: citation and reason (srcrm.<id>.what / srcrm.<id>.why)
REMOVED_SOURCES = {
    "tudor": "Tudor-Locke 2008",
    "nsf_dur": "National Sleep Foundation 2015",
    "keytel": "Keytel 2005",
    "edwards": "Edwards 1993",
    "buchheit": "Buchheit 2014",
    "acwr": None,
    "frankenfield": "Frankenfield 2005",
    "iom_cho": "IOM 2005",
}


def src(*keys):
    """Sources for the page: original citation, link, type and conflicts (codes); summary, notes and reason from the catalogs."""
    out = []
    for k in keys:
        s = SOURCES[k]
        item = {"key": k, **s, "what": M(f"src.{k}.what"), "note": M(f"src.{k}.note")}
        if s.get("flagged"):
            item["why"] = M(f"src.{k}.why")
        out.append(item)
    return out


def removed_sources():
    return [{"key": k, "title": cite, "what": M(f"srcrm.{k}.what"), "why": M(f"srcrm.{k}.why")}
            for k, cite in REMOVED_SOURCES.items()]


# Metrics computed by the band with unpublished methods
ZEPP_NOTE = {"label": M("ui.health.zepp_algorithm"), "text": M("note.zepp"), "sources": src("concheiro")}
PAI_NOTE = {"label": M("ui.fitness.licensed_algorithm"), "text": M("note.pai"), "sources": src("pai", "who")}


def band_of(v, bands):
    for b in bands:
        if b[0] <= v < b[1]:
            return b
    return bands[-1]


def item(key, value, unit, period, bands, scale, text, sources, n, need, fmt=None):
    """One item of the Health tab. Title and texts are keys h.<key>.*; bands: (from, to, label, level)."""
    it = {"key": key, "title": M(f"h.{key}.title"), "unit": unit, "period": period, "sources": src(*sources),
          "scale": scale, "bands": [{"from": b[0], "to": b[1], "label": M(f"h.band.{b[2]}"), "level": b[3]} for b in bands],
          "days": n, "need": need}
    if value is None or n < need:
        it.update({"value": None, "status": "collecting", "label": M("health.need_days", need=need, n=n),
                   "text": text["base"]})
        return it
    b = band_of(value, bands)
    it.update({"value": round(value, 1) if isinstance(value, float) else value,
               "display": fmt(value) if fmt else None,
               "status": "ok", "level": b[3], "label": M(f"h.band.{b[2]}"),
               "text": J(text["base"], text.get(b[3]))})
    return it


def training_type(t):
    """Type of a workout: the confirmed label, otherwise the suggestion."""
    return (t.get("label") or {}).get("type") or (t.get("suggestion") or {}).get("type")


def health(con, d):
    hist = history(con, d, 7)
    data_days = [x for x in hist if x["has_data"]]
    sleeps = [sleep_for(con, d - timedelta(days=i)) for i in range(6, -1, -1)]
    sleeps = [x for x in sleeps if x]
    rests = [x["resting"] for x in hist if x["resting"]]
    band_rest = all(x["resting_src"] == "band" for x in hist if x["resting"])
    items = []
    avg_nights = M("h.period.avg7_nights")

    # days in progress or incomplete would distort the step averages: only finished days are used
    full = [x for x in data_days if x["date"] != d.isoformat() or d < now().date()]
    step_days = full or data_days
    steps_avg = round(mean([x["steps"] for x in step_days])) if step_days else None
    items.append(item(
        "steps", steps_avg, M("u.steps"), M("h.period.full_days"),
        [(0, 5000, "low", 0), (5000, 8000, "intermediate", 2), (8000, 10000, "good", 3), (10000, 10**6, "excellent", 4)],
        [0, 15000],
        {"base": M("h.steps.base"), 0: M("h.steps.0"), 2: M("h.steps.2")},
        ["paluch"], len(step_days), 3))

    zone7 = sum(x["zone_minutes"] for x in hist)
    if data_days and len(data_days) < 7:
        zone7 = round(zone7 * 7 / len(data_days))
    items.append(item(
        "activity", zone7 if data_days else None, M("unit.min_week"),
        M("h.period.last7") if len(data_days) >= 7 else M("h.period.last7_projected"),
        [(0, 75, "far_below_guidelines", 0), (75, 150, "below_guidelines", 1),
         (150, 300, "within_guidelines", 3), (300, 10**6, "above_guidelines", 4)],
        [0, 450],
        {"base": M("h.activity.base"), 4: M("h.activity.4"), 0: M("h.activity.0")},
        ["who"], len(data_days), 3))

    strength_days = sum(1 for x in hist if any(training_type(t) == "strength" for t in x["trainings"]))
    strength_min = sum(t["duration_min"] for x in hist for t in x["trainings"] if training_type(t) == "strength")
    items.append(item(
        "strength", strength_days if data_days else None, M("unit.days_week"), M("h.period.last7"),
        [(0, 2, "below_guidelines", 1), (2, 8, "within_guidelines", 4)],
        [0, 7],
        {"base": M("h.strength.base", min=strength_min), 1: M("h.strength.1")},
        ["who", "momma"], len(data_days), 3))

    rest_avg = round(mean(rests)) if rests else None
    items.append(item(
        "rhr", rest_avg, M("u.bpm"), M("h.period.avg7"),
        [(0, 60, "excellent", 4), (60, 70, "good", 3), (70, 80, "average", 2), (80, 90, "elevated", 1), (90, 250, "high", 0)],
        [40, 100],
        {"base": J(M("h.rhr.base"), None if band_rest else M("h.rhr.estimated")), 0: M("h.rhr.0"), 1: M("h.rhr.1")},
        ["zhang"], len(rests), 3))

    sl_avg = mean([x["total"] for x in sleeps]) / 60 if sleeps else None
    items.append(item(
        "sleep", sl_avg, M("unit.hours"), avg_nights,
        [(0, 6, "short", 0), (6, 7, "a_bit_short", 2), (7, 9, "in_reference", 4),
         (9, 10, "a_bit_long", 2), (10, 24, "long", 1)],
        [4, 11],
        {"base": M("h.sleep.base"), 0: M("h.sleep.0")},
        ["cappuccio", "wear"], len(sleeps), 3, lambda v: M("fmt.dur", v=round(v * 60))))

    weak = M("h.sleep_weak")
    eff = round(mean([x["efficiency"] for x in sleeps])) if sleeps else None
    items.append(item(
        "efficiency", eff, "%", avg_nights,
        [(0, 85, "below_reference", 1), (85, 101, "good", 4)],
        [60, 100],
        {"base": J(M("h.efficiency.base"), weak)},
        ["nsf_q", "concheiro"], len(sleeps), 3))

    waso = round(mean([x["awake"] for x in sleeps])) if sleeps else None
    items.append(item(
        "waso", waso, M("unit.min"), avg_nights,
        [(0, 21, "in_reference", 4), (21, 10**4, "above_reference", 1)],
        [0, 60],
        {"base": J(M("h.waso.base"), weak)},
        ["nsf_q", "concheiro"], len(sleeps), 3))

    lat = round(mean([x["latency"] for x in sleeps])) if sleeps else None
    items.append(item(
        "latency", lat, M("unit.min"), avg_nights,
        [(0, 31, "in_reference", 4), (31, 10**4, "above_reference", 1)],
        [0, 60],
        {"base": J(M("h.latency.base"), weak)},
        ["nsf_q", "concheiro"], len(sleeps), 3))

    ready = [i for i in items if i["status"] == "ok"]
    return {
        "items": items,
        "strengths": [i["title"] for i in ready if i["level"] >= 3],
        "improve": [i["title"] for i in ready if i["level"] <= 1],
        "middle": [i["title"] for i in ready if i["level"] == 2],
        "notes": {"stages": src("wear")[0], "load": src("impellizzeri")[0]},
    }


# ---------------------------------------------------------------- comments (insights)
# Sentences generated by fixed rules with thresholds taken from the sources in SOURCES: no opaque
# statistical model, every sentence can be traced back to a number and a threshold.

def sleep_highlights(sl):
    """Key points of the night: duration from Cappuccio 2010 (independent); latency, wake and efficiency from the
    NSF 2017 consensus and stages from Ohayon 2004, both flagged for conflicts of interest (no verifiable alternative)."""
    if not sl:
        return []
    tst = sl["total"] or 1
    deep_pct, rem_pct = 100 * sl["deep"] / tst, 100 * sl["rem"] / tst

    def h(key, display, ok, sources, info=False):
        return {"key": key, "title": M(f"shl.{key}.title"), "display": display, "ok": ok, "ref": M(f"shl.{key}.ref"),
                "label": M("shl.ideal") if ok else (M("shl.atypical") if info else M("shl.less_ideal")),
                "level": 4 if ok else (2 if info else 1), "sources": src(*sources)}

    dur = lambda m: M("fmt.dur", v=m)  # noqa: E731
    return [
        h("duration", dur(sl["total"]), 420 <= sl["total"] <= 540, ["cappuccio"]),
        h("latency", dur(sl["latency"]), sl["latency"] <= 30, ["nsf_q", "concheiro"]),
        h("waso", dur(sl["awake"]), sl["awake"] <= 20, ["nsf_q", "concheiro"]),
        h("efficiency", M("fmt.pct", v=sl["efficiency"]), sl["efficiency"] >= 85, ["nsf_q", "concheiro"]),
        h("deep", M("shl.stage_value", pct=deep_pct, min=sl["deep"]), 13 <= deep_pct <= 25,
          ["ohayon", "concheiro"], info=True),
        h("rem", M("shl.stage_value", pct=rem_pct, min=sl["rem"]), 18 <= rem_pct <= 26,
          ["ohayon", "concheiro"], info=True),
    ]


def sleep_insight(sl):
    """Comment on the night: code = short, fragmented, good, average (the tone depends on the code)."""
    if not sl:
        return None
    n, awake, tot = sl.get("awakenings", 0), sl["awake"], sl["total"]
    if tot < 360:
        code, params = "short", {"total": tot}
    elif n >= 8 or awake > 45:
        code, params = "fragmented", {"n": n, "awake": awake}
    elif 420 <= tot <= 540 and sl["efficiency"] >= 85 and awake <= 20 and sl["latency"] <= 30:
        code, params = "good", {"total": tot, "eff": sl["efficiency"]}
    else:
        code, params = "average", {"total": tot, "n": n, "awake": awake}
    return {"code": code, "title": M(f"si.{code}.title"), "text": M(f"si.{code}.text", **params)}


SLEEP_TONE = {"good": "good", "short": "warn", "fragmented": "warn", "average": "info"}


def day_insights(s):
    """Comments for the Today screen, by importance. tag: code of the section (tag.* in the page)."""
    out = []
    base = s.get("baselines") or {}
    rb, hb = base.get("rhr") or {}, base.get("hrv") or {}
    if rb.get("mean") is not None and s["hr"]["resting"] and s["hr"]["resting"] > rb["high"]:
        out.append({"tag": "vitals", "tone": "warn", "title": M("ins.rhr_high.title"),
                    "text": M("ins.rhr_high.text", v=s["hr"]["resting"], lo=rb["low"], hi=rb["high"])})
    if hb.get("status") == "below":
        out.append({"tag": "recovery", "tone": "warn", "title": M("ins.hrv_low.title"),
                    "text": M("ins.hrv_low.text", avg=hb["avg7"], lo=hb["low"], hi=hb["high"])})
    big = max(s.get("trainings") or s["workouts"], key=lambda w: w.get("trimp", 0), default=None)
    if s["active_min"] >= 120:
        out.append({"tag": "fitness", "tone": "good", "title": M("ins.big_day.title"),
                    "text": M("ins.big_day.text", active=s["active_min"], steps=s["steps"], zone=s["zone_minutes"])})
    elif big and big["duration_min"] >= 20:
        out.append({"tag": "fitness", "tone": "good",
                    "title": M("ins.workout.title", name=big["name"], dur=big["duration_min"]),
                    "text": M("ins.workout.text", avg=big["avg_hr"], max=big["max_hr"], kcal=big["kcal"])})
    sl = s.get("sleep")
    if sl:
        si = sleep_insight(sl)
        out.append({"tag": "sleep", "tone": SLEEP_TONE[si["code"]], "title": si["title"],
                    "text": J(si["text"], M("ins.zepp_score", score=sl["score"]) if sl.get("score") else None)})
    if s["zone_minutes_week"] >= s["goals"]["zone_week"]:
        out.append({"tag": "cardio", "tone": "good", "title": M("ins.who_week.title"),
                    "text": M("ins.who_week.text", min=s["zone_minutes_week"], goal=s["goals"]["zone_week"])})
    if s["steps"] >= s["goals"]["steps"]:
        out.append({"tag": "activity", "tone": "good", "title": M("ins.steps_goal.title"),
                    "text": M("ins.steps_goal.text", steps=s["steps"], goal=s["goals"]["steps"])})
    nut = s.get("nutrition") or {}
    if nut.get("issues"):
        c = nut["issues"][0]
        out.append({"tag": "nutrition", "tone": "warn", "title": M("ins.nut.title", name=c["title"], label=c["label"]),
                    "text": J(M("ins.nut.text", n=c["days"], value=c["display"], target=c["target"]),
                              None if c["status"] == "certain" else M("ins.nut.probable"))})
    nfix = len(s["quality"]["hr_fixes"])
    if nfix:
        out.append({"tag": "data", "tone": "info", "title": M("ins.hr_fixes.title", n=nfix), "text": M("ins.hr_fixes.text")})
    return out


VITALS = {
    # key: (title, unit, decimals, minimum resolution of the band)
    "rhr": (M("vit.rhr.title"), "bpm", 0, 1.0),
    "hrv": (M("vit.hrv.title"), "ms", 0, 0.02),
    "resp": (M("vit.resp.title"), M("unit.breaths_min"), 1, 0.5),
    "spo2": (M("vit.spo2.title"), "%", 0, 1.0),
    "temp": (M("vit.temp.title"), "°C", 1, 0.2),
}


def vitals(d, days=30):
    """Daily series of the vital signs with the personal range.

    Resting heart rate and HRV: AIS baseline (60-day mean ± 0.5 standard deviations, HRV on ln).
    Breathing, SpO2 and temperature: 60-day mean ± 1 standard deviation, because for these
    signs what matters is spotting a night outside your usual (for example at the start of an illness).
    """
    with closing(connect()) as con:
        dates = [d - timedelta(days=i) for i in range(BASE_DAYS - 1, -1, -1)]
        raw = {k: [] for k in VITALS}
        for x in dates:
            rs = series(con, "HUAMI_HEART_RATE_RESTING_SAMPLE", "HEART_RATE", midnight(x),
                        midnight(x + timedelta(days=1)), HR_MIN, HR_MAX)
            raw["rhr"].append(rs[-1][1] if rs else None)
            sl = sleep_for(con, x)
            for k in ("hrv", "resp", "spo2", "temp"):
                raw[k].append(sl.get(k) if sl else None)
    out = {}
    for k, (title, unit, dec, floor) in VITALS.items():
        vals = raw[k]
        if k in ("rhr", "hrv"):
            b = baseline(vals, log=(k == "hrv"), floor=floor)
        else:
            present = [v for v in vals if v is not None]
            b = {"today": vals[-1], "n_base": len(present), "need": BASE_MIN, "mean": None, "low": None,
                 "high": None, "avg7": None, "status": "collecting"}
            if len(present) >= BASE_MIN:
                m, sd = mean(present), statistics.stdev(present)
                w = max(sd, floor)
                b.update({"mean": round(m, 1), "low": round(m - w, 1), "high": round(m + w, 1)})
                if vals[-1] is not None:
                    b["status"] = "above" if vals[-1] > m + w else "below" if vals[-1] < m - w else "normal"
        last = next(((dates[i].isoformat(), vals[i]) for i in range(len(vals) - 1, -1, -1) if vals[i] is not None), None)
        out[k] = {"title": title, "unit": unit, "decimals": dec,
                  "series": [[dates[i].isoformat(), vals[i]] for i in range(len(vals) - days, len(vals))],
                  "last": last, **{f: b.get(f) for f in ("mean", "low", "high", "avg7", "status", "n_base", "need")}}
    return out


def summary(d):
    with closing(connect()) as con:
        p = profile(con, d)
        day = compute_day(con, d, p)
        hist = history(con, d, 28)
        prev14 = hist[-15:-1]
        sleep = sleep_for(con, d)
        load = load_status(hist)

        hr_buckets = {}
        for r in day["rows"]:
            if r["hr"] is not None:
                hr_buckets.setdefault(r["t"] // 300 * 300, []).append(r["hr"])
        start, end = day["start"], midnight(d + timedelta(days=1))
        # one bucket per hour the day really has: 25 on the day the clock goes back (the repeated hour twice),
        # 23 when it goes forward; hour_labels has the local hour of each bucket
        hour_starts = range(start, end, 3600)  # elapsed hours from local midnight
        by_hour = [0] * len(hour_starts)
        for r in day["rows"]:
            if not r.get("jump") and start <= r["t"] < end:
                by_hour[(r["t"] - start) // 3600] += r["steps"]  # elapsed hours

        last_hr = next(({"t": r["t"], "v": r["hr"]} for r in reversed(day["rows"]) if r["hr"] is not None), None)
        stress = stress_summary(series(con, "HUAMI_STRESS_SAMPLE", "STRESS", start, end, 0, 100))
        spo2 = spo2_clean(series(con, "HUAMI_SPO2_SAMPLE", "SPO2", start, end, 70, 100), day["rows"])
        base = baselines(con, d)
        hrv_day = [v for _, v in series(con, "GENERIC_HRV_VALUE_SAMPLE", "VALUE", start, end, 5, 250)]
        battery = latest(con, "BATTERY_LEVEL", "LEVEL", 0, 100, before=end)
        pai = pai_for(con, d)
        last_any = con.execute(f'SELECT MAX(TIMESTAMP) FROM "{ACT}" WHERE DEVICE_ID=?', (DEVICE_ID,)).fetchone()[0] \
            if columns(con, ACT) else None
        vo2 = vo2max_summary(con, d, p)
        sri = sleep_regularity(con, d)
        nut = nutrition_brief(nutrition_analysis(con, d, p, hist))

    week_zone = sum(x["zone_minutes"] for x in hist[-7:])
    hrs = day["hrs"]
    raw_hrs = [r["hr_raw"] for r in day["rows"] if r.get("hr_raw") is not None]
    res = {
        "date": d.isoformat(),
        "tz": str(TZ),
        "day_start": day["start"],
        "day_end": end,
        "profile": {k: p.get(k) for k in ("age", "sex", "weight", "weight_src", "weight_date", "height", "hrmax", "hrmax_src",
                                          "hrmax_pred", "hrmax_obs", "hrmax_obs_at", "bmr", "missing", "stride_m",
                                          "work_level")},
        "goals": {"steps": p["steps_goal"], "active": p["active_goal"], "sleep": p["sleep_goal_min"],
                  "zone_week": p["zone_goal_week"], "stride_m": p["stride_m"]},
        "plan_today": plan_for(d)["text"] or None,
        "steps": day["steps"], "steps_band": day["steps_band"], "jumps": day["jumps"], "distance_km": day["distance_km"],
        "active_min": day["active_min"], "active_steps": day["active_steps"],
        "kcal": day["kcal"],
        "zones": day["zones"],
        "zone_bounds": [round(p["hrmax"] * x) for x in ZONE_PCT],
        "zone_minutes": day["zone_minutes"], "zone_minutes_week": week_zone,
        "hr": {
            "last": last_hr,
            "min": min(hrs) if hrs else None, "max": max(hrs) if hrs else None,
            "avg": rnd(mean(hrs)),
            "raw_min": min(raw_hrs) if raw_hrs else None, "raw_max": max(raw_hrs) if raw_hrs else None,
            "resting": day["resting"], "resting_src": day["resting_src"],
            "series": [[t, round(mean(v))] for t, v in sorted(hr_buckets.items())],
        },
        "steps_by_hour": by_hour,
        "hour_labels": [datetime.fromtimestamp(t, TZ).hour for t in hour_starts],
        "workouts": day["sessions"],
        "trainings": day["trainings"],
        "activity_types": [{"key": k, "name": v[0], "load": k in LOAD_TYPES} for k, v in ACTIVITY_TYPES.items()],
        "strength_day_loads": {str(n): strength_day_load(n) for n in (1, 2, 3)},  # default added load of each strength day
        "plan": plan_for(d),
        "sleep": sleep,
        "hrv": {"night": sleep["hrv"] if sleep else None, "day": rnd(mean(hrv_day))} if (hrv_day or sleep) else None,
        "stress": stress,
        "spo2": spo2 if spo2 and spo2["avg"] is not None else None,
        "pai": pai,
        "baselines": base,
        "quality": {"hr_fixes": hr_fixes(day["rows"]), "hr_samples": len(day["hrs"]) + len(hr_fixes(day["rows"])),
                    "spo2_motion": spo2["motion_removed"] if spo2 else 0,
                    "spo2_dips": spo2["isolated_dips"] if spo2 else 0,
                    "spo2_samples": spo2["n"] if spo2 else 0,
                    "rules": [{"id": r["id"], "title": M(f"rule.{r['id']}.title"), "text": M(f"rule.{r['id']}.text"),
                               "sources": src(*r["sources"])} for r in CLEAN_RULES]},
        "readiness": readiness(day, prev14, sleep, load, p, base),
        "load": {k: v for k, v in load.items() if k != "series"},
        "advice": daily_advice(plan_for(d), base, p),
        "vo2max": vo2,
        "sri": sri,
        "proprietary": {"stress": ZEPP_NOTE, "sleep_score": ZEPP_NOTE, "pai": PAI_NOTE},
        "kcal_sources": src("ferri_marini", "looney", "weir", "compendium", "saeidifard", "pandolf", "drain", "madden", "fuller",
                            "odriscoll"),
        "work_levels": work_levels(),
        "hrmax_sources": src("tanaka", "mohoric", "bent"),
        "trimp": day["trimp"],
        "load_note": {"text": M("load.note"), "sources": src("morton", "impellizzeri")},
        "sources_index": {"sources": src(*SOURCES), "removed": removed_sources()},
        "battery": battery,
        "last_sample": last_any,
        "samples": len(day["rows"]),
        "nutrition": nut,
    }
    res["sleep_insight"] = sleep_insight(sleep)
    res["sleep_highlights"] = sleep_highlights(sleep)
    res["insights"] = day_insights(res)
    return res


def trends(d):
    with closing(connect()) as con:
        p = profile(con, d)
        hist = history(con, d, 28)
        sleeps = [sleep_for(con, d - timedelta(days=i)) for i in range(6, -1, -1)]
        report = health(con, d)
    last7 = hist[-7:]
    load = load_status(hist)
    return {
        "goals": {"steps": p["steps_goal"], "sleep": p["sleep_goal_min"], "zone_week": p["zone_goal_week"]},
        "days": [{"date": x["date"], "steps": x["steps"], "jumps": x["jumps"], "kcal": x["kcal"]["total"] if x["has_data"] else 0,
                  "active_kcal": x["kcal"]["active"], "zone_minutes": x["zone_minutes"], "resting": x["resting"],
                  "active_min": x["active_min"], "workouts": len(x["sessions"]), "trimp": x["trimp"],
                  "bmr_kcal": x["kcal"]["bmr"] if x["has_data"] else 0,
                  "sleep": s["total"] if s else None}
                 for x, s in zip(last7, sleeps)],
        "load": load,
        "health": report,
    }


# ---------------------------------------------------------------- advice of the day from HRV
def daily_advice(plan, base, p):
    """Advice on the day's programme from the 7-day HRV average compared with your normal range.

    Rule of the randomised trials (Vesterinen 2016) and of the AIS 2023 guidelines: within the normal range follow the plan,
    below it only low intensity or rest. For strength the studies show no difference (Bittencourt 2024):
    the session stays, at most with less volume. 7-day average above the range: studies disagree, follow the plan.
    """
    hb = (base or {}).get("hrv") or {}
    rb = (base or {}).get("rhr") or {}
    types = plan.get("types") or []
    sources = src("ais_hrv", "vesterinen", "granero")
    def out(status, code, text, extra=()):
        return {"status": status, "title": M(f"adv.{code}.title"), "text": text, "sources": sources + src(*extra)}

    if not types:
        if plan.get("rest"):
            return out("rest", "rest", M("adv.rest.text"))
        return None
    if types == ["work"]:
        return out("work", "active_day", M("adv.active_day.text"))
    if hb.get("mean") is None:
        return out("collecting", "collecting", M("adv.collecting.text", need=hb.get("need", BASE_MIN), n=hb.get("n_base", 0)))
    if hb.get("avg7") is None:
        return out("collecting", "few_nights", M("adv.few_nights.text"))
    vals = {"avg": hb["avg7"], "lo": hb["low"], "hi": hb["high"]}
    extra = M("adv.rhr_also_high") if rb.get("status") == "above" else None
    if hb["status"] == "normal":
        return out("follow", "follow", J(M("adv.follow.in_band", **vals), extra))
    if hb["status"] == "above":
        return out("follow", "follow", J(M("adv.follow.above", **vals), extra))
    if "strength" in types:
        return out("light", "less_volume", J(M("adv.less_volume.text", **vals), extra), ("bittencourt",))
    easy = round(p["hrmax"] * ZONE_PCT[2])
    return out("light", "easy", J(M("adv.easy.text", easy=easy, **vals), extra))


# ---------------------------------------------------------------- estimated VO2max
def vo2max_summary(con, d, p):
    series_ = []
    for i in range(11, -1, -1):
        x = d - timedelta(days=7 * i)
        v, _, _ = vo2max_for(con, x, p["hrmax"])
        series_.append([x.isoformat(), v])
    v = p.get("vo2max_est")
    res = {"value": v, "series": series_, "hrmax": p["hrmax"], "hrmax_src": p["hrmax_src"],
           "hrmax_pred": p["hrmax_pred"], "rest": p.get("vo2max_rest"), "see": sci.VO2MAX_SEE,
           "sources": src("uth", "esco", "han", "wehrlin", "tanaka")}
    if v is None:
        res["text"] = M("vo2.need_text")
        return res
    res.update({
        "low": round(v - sci.VO2MAX_SEE, 1), "high": round(v + sci.VO2MAX_SEE, 1), "met": round(v / sci.MET_ML, 1),
        "text": J(M("vo2.text"), M("vo2.hrmax_observed") if p["hrmax_src"] == "observed" else M("vo2.hrmax_age"),
                  M("vo2.met")),
    })
    # altitude goal: only if you set it in the profile
    alt = p.get("altitude_goal_m")
    if alt:
        km = max(0.0, alt / 1000 - 0.3)
        frac = sci.altitude_fraction(alt)
        res["altitude"] = {"m": alt, "name": p.get("altitude_goal_name"), "fraction": round(frac, 2),
                           "value": round(v * frac, 1), "frac_low": round(max(0.0, 1 - 0.075 * km), 2),
                           "frac_high": round(max(0.0, 1 - 0.046 * km), 2), "extrapolated": alt > 2800}
    return res


# ---------------------------------------------------------------- sleep regularity
SRI_MIN_PAIRS = 120 * 60   # at least 120 hours of valid pairs 24 hours apart
_sricache = Memo(200)


def clock_minutes(d):
    """Instant of each minute of the local clock of day d, 00:00 to 23:59 (1440 values, None for a minute the
    clock skips). On the day the clock goes back the repeated hour counts once, its first occurrence: the days
    are compared at the same clock time, as people live them."""
    a, b = midnight(d), midnight(d + timedelta(days=1))
    if b - a == 86400:  # elapsed: a day without a clock change, every minute once
        return list(range(a, b, 60))
    out = []
    for m in range(1440):
        t = at(d, m // 60, m % 60)
        local = datetime.fromtimestamp(t, TZ)
        out.append(t if (local.date(), local.hour, local.minute) == (d, m // 60, m % 60) else None)
    return out


_statecache = Memo(400)


def day_states(con, x):
    """Asleep (0), awake (1) or without data (None) for every minute of the local clock of day x (clock_minutes),
    from the nights ending on x and on the day after (band summaries or samples) and the kind of each minute. A past
    day is computed again only if its rows change."""
    if x >= now().date():
        return _statecache.get((DB, x.isoformat(), "today"), lambda: _day_states(con, x), version=now_version(con))
    a, b = at(x - timedelta(days=1), 18), at(x + timedelta(days=1), 12)
    return by_data(_statecache, (DB, x.isoformat()), con, lambda: _day_states(con, x),
                   lambda r: [(ACT, a, b), ("HUAMI_SLEEP_SESSION_SAMPLE", midnight(x), midnight(x + timedelta(days=2)))])


def _day_states(con, x):
    start, end = midnight(x), midnight(x + timedelta(days=1))
    asleep = set()
    for night in (x, x + timedelta(days=1)):
        sl = sleep_for(con, night)
        if sl:
            for a, b, st in sl["timeline"]:
                if st != "awake":
                    asleep.update(range(a // 60 * 60, b, 60))
    cols = columns(con, ACT)
    kinds = {}
    if {"TIMESTAMP", "STEPS", "HEART_RATE"} <= cols:
        kind = '"RAW_KIND"' if "RAW_KIND" in cols else "0"
        kinds = dict(con.execute(f'SELECT TIMESTAMP, {kind} FROM "{ACT}" WHERE DEVICE_ID=? AND TIMESTAMP>=? '
                                 'AND TIMESTAMP<?', (DEVICE_ID, start, end)).fetchall())

    def state(t):
        k = kinds.get(t)
        if t in asleep or k == KIND_SLEEP:
            return 0
        if k is not None and k not in (KIND_NOT_WORN, KIND_CHARGING):
            return 1
        return None
    return [None if t is None else state(t) for t in clock_minutes(x)]


def sri_window(con, d0, d1):
    days, x = [], d0
    while x <= d1:
        days.append(day_states(con, x))
        x += timedelta(days=1)
    v, pairs = sci.sleep_regularity_index(days)
    return (round(v) if v is not None and pairs >= SRI_MIN_PAIRS else None), pairs


def sleep_regularity(con, d, days=14):
    """Sleep Regularity Index of the last days and of the ones before, with the times of sleep and waking."""
    key = (DB, d.isoformat(), days)
    if d >= now().date():
        return _sricache.get(key + ("today",), lambda: _sleep_regularity(con, d, days), version=now_version(con))
    # it reads the nights (band summaries and samples) from the day before the first window to the day after d
    a, b = midnight(d - timedelta(days=2 * days)), midnight(d + timedelta(days=2))
    return by_data(_sricache, key, con, lambda: _sleep_regularity(con, d, days),
                   lambda r: [(ACT, a, b), ("HUAMI_SLEEP_SESSION_SAMPLE", a, b)])


def _sleep_regularity(con, d, days):
    d0 = d - timedelta(days=days - 1)
    value, pairs = sri_window(con, d0, d)
    prev, _ = sri_window(con, d0 - timedelta(days=days), d0 - timedelta(days=1))
    onsets, wakes = [], []
    x = d0
    while x <= d:
        sl = sleep_for(con, x)
        if sl:
            for t, bucket in ((sl["start"], onsets), (sl["end"], wakes)):
                dt = datetime.fromtimestamp(t, TZ)
                bucket.append(dt.hour * 60 + dt.minute)
        x += timedelta(days=1)
    om, osd = sci.circular_sd_minutes(onsets)
    wm, wsd = sci.circular_sd_minutes(wakes)
    res = {"value": value, "prev": prev, "pairs_h": round(pairs / 60), "need_h": SRI_MIN_PAIRS // 60, "days": days,
           "onset": {"mean": om, "sd": osd}, "wake": {"mean": wm, "sd": wsd}, "nights": len(onsets),
           "text": M("sri.text"),
           "sources": src("fischer", "chaput", "concheiro")}
    return res


# ---------------------------------------------------------------- weekly summary


def day_status(x, today, types, trainings):
    done = {training_type(t) for t in trainings}
    if x > today:
        return "futuro"
    if not types:
        return "extra" if trainings else "rest"
    if types == ["work"]:
        return "done" if "work" in done else ("today" if x == today else "missing")
    if done & set(types):
        return "done"
    if trainings:
        return "different"
    return "today" if x == today else "skipped"


def week_stats(con, a, b):
    today = now().date()
    days, x = [], a
    while x <= b:
        days.append(x)
        x += timedelta(days=1)
    hist = [compute_day(con, x, profile(con, x)) for x in days if x <= today]
    sleeps = [sleep_for(con, x) for x in days if x <= today]
    sleeps = [sl for sl in sleeps if sl]
    rows_ = []
    for x in days:
        h = next((y for y in hist if y["date"] == x.isoformat()), None)
        pl = plan_for(x)
        trs = h["trainings"] if h else []
        rows_.append({"date": x.isoformat(), "plan": pl["text"], "types": pl["types"],
                      "done": [{"name": t["name"], "type": training_type(t), "confirmed": bool(t.get("label")),
                                "duration_min": t["duration_min"]} for t in trs],
                      "status": day_status(x, today, pl["types"], trs)})
    planned = [r for r in rows_ if r["status"] in ("done", "different", "skipped") and r["types"] != ["work"]]
    complete = [y for y in hist if y["has_data"] and (y["date"] != today.isoformat())]
    rests = [y["resting"] for y in hist if y["resting"] and y["resting_src"] == "band"]
    hrvs = [sl["hrv"] for sl in sleeps if sl.get("hrv")]
    w = weight_for(b)
    sri = sri_window(con, a, min(b, today))[0] if len(days) >= 7 and b <= today else None
    return {
        "start": a.isoformat(), "end": b.isoformat(), "days": rows_, "data_days": sum(1 for y in hist if y["has_data"]),
        "plan_done": sum(1 for r in planned if r["status"] == "done"), "plan_total": len(planned),
        "zone_minutes": sum(y["zone_minutes"] for y in hist),
        "strength_days": sum(1 for y in hist if any(training_type(t) == "strength" for t in y["trainings"])),
        "strength_min": sum(t["duration_min"] for y in hist for t in y["trainings"] if training_type(t) == "strength"),
        "trimp": sum(y["trimp"] for y in hist),
        "steps_avg": round(mean([y["steps"] for y in complete])) if complete else None,
        "jumps": sum(y["jumps"] for y in hist),
        "kcal_avg": round(mean([y["kcal"]["total"] for y in complete])) if complete else None,
        "sleep_avg": round(mean([sl["total"] for sl in sleeps])) if sleeps else None, "nights": len(sleeps),
        "rhr_avg": round(mean(rests), 1) if rests else None,
        "hrv_avg": round(mean(hrvs)) if hrvs else None,
        "sri": sri,
        "weight": {"date": w[0], "kg": w[1]} if w else None,
    }


def week_summary(d, which="auto"):
    """Week from Monday to Sunday. "auto": on Monday it shows the week just ended, on the other days the current one."""
    today = now().date()
    mon = d - timedelta(days=d.weekday())
    if which == "previous" or (which == "auto" and d.weekday() == 0):
        mon -= timedelta(days=7)
    sun = mon + timedelta(days=6)
    with closing(connect()) as con:
        cur = week_stats(con, mon, sun)
        prev = week_stats(con, mon - timedelta(days=7), mon - timedelta(days=1))
    diff = {}
    for k in () if not prev["data_days"] else ("zone_minutes", "strength_days", "trimp", "steps_avg", "sleep_avg", "rhr_avg", "hrv_avg", "sri"):
        if cur.get(k) is not None and prev.get(k) is not None:
            diff[k] = round(cur[k] - prev[k], 1)
    if cur["weight"] and prev["weight"]:
        diff["weight"] = round(cur["weight"]["kg"] - prev["weight"]["kg"], 1)
    return {"start": mon.isoformat(), "end": sun.isoformat(), "complete": sun < today,
            "which": "previous" if sun < d else "current",
            "current": cur, "previous": prev, "diff": diff,
            "targets": {"zone_week": 150, "zone_week_max": 300, "strength_days": 2, "strength_min": [30, 60], "sleep_h": 7},
            "sources": src("who", "momma", "cappuccio", "paluch", "chaput")}


# ---------------------------------------------------------------- strength log
def exercise_kind(name):
    n = name.lower()
    # Italian and English words: the exercise name is written by the user
    if any(w in n for w in ("corda", "stretching", "riscaldamento", "camminata", "corsa", "nuoto", "mobilità",  # keywords
                            "rope", "warm-up", "warm up", "walk", "run", "swim", "mobility")):
        return "skip"
    if "plank" in n or "isometr" in n or "wall sit" in n or "hold" in n:
        return "time"
    if re.search(r"pull|trazion|dips|chin|piegament.*parallel", n):
        return "system"
    return "reps"


def default_kg(name):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*kg", name.lower())
    return float(m.group(1).replace(",", ".")) if m else 0.0


def strength_load():
    data = state_load("strength.json")
    return {k: v for k, v in data.items() if isinstance(k, str) and DATE_RE.match(k) and isinstance(v, dict)}


def clean_sets(sets):
    out = []
    for st in sets if isinstance(sets, list) else []:
        if not isinstance(st, dict):
            raise UserError("e.set_invalid")
        reps, kg, sec = st.get("reps"), st.get("kg"), st.get("sec")
        for v, lo, hi, what in ((reps, 0, 200, "reps"), (kg, 0, 300, "kg"), (sec, 0, 3600, "sec")):
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi
                                  or v != v):
                raise UserError(f"e.set_{what}")
        if reps is None and sec is None:
            continue
        out.append({"reps": int(reps) if reps is not None else None, "kg": round(float(kg), 2) if kg else 0.0,
                    "sec": int(sec) if sec is not None else None})
    if len(out) > 15:
        raise UserError("e.sets_max", max=15)
    return out


def save_strength(day, entries, load_kg=None):
    """Saves the day's sets. load_kg: added load of the session (same for every exercise and the plank);
    if given, it applies to every set and also updates the strength workout confirmed that day."""
    if day > now().date():
        raise UserError("e.future_training")
    if not isinstance(entries, list) or len(entries) > 25:
        raise UserError("e.exercises_invalid")
    if load_kg is not None and valid_load(load_kg) is None:
        raise UserError("e.load_range", max=LOAD_MAX)
    clean = []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not 0 < len(e["name"].strip()) <= 80:
            raise UserError("e.exercise_name")
        sets = clean_sets(e.get("sets"))
        if load_kg is not None:
            for st in sets:
                st["kg"] = valid_load(load_kg)
        if sets:
            clean.append({"name": e["name"].strip(), "sets": sets})
    with _labels_lock:
        data = strength_load()
        if clean:
            rec = {"entries": clean, "saved": int(time.time())}
            if load_kg is not None:
                rec["load_kg"] = valid_load(load_kg)
            data[day.isoformat()] = rec
        else:
            data.pop(day.isoformat(), None)
        state_save("strength.json", data)
        if clean and load_kg is not None:
            labels = labels_load()
            changed = False
            for lb in labels.values():
                if lb.get("type") == "strength" and midnight(day) <= lb.get("start", 0) < midnight(day + timedelta(days=1)) \
                        and lb.get("load_kg") != valid_load(load_kg):
                    lb["load_kg"] = valid_load(load_kg)
                    changed = True
            if changed:
                labels_save(labels)
                _daycache.clear()
    return {"ok": True}


def strength_labels_on(d):
    """Strength workouts confirmed on day d."""
    a, b = midnight(d), midnight(d + timedelta(days=1))
    return [lb for lb in labels_view().values() if lb.get("type") == "strength" and a <= lb.get("start", 0) < b]


def body_weight_on(con, x):
    return profile(con, x)["weight"]


def session_metrics(kind, sets, bw):
    """Numbers of one exercise in a session. volume = reps × added kg; hold = seconds × kg (plank)."""
    reps = sum(st["reps"] or 0 for st in sets)
    secs = sum(st["sec"] or 0 for st in sets)
    best = None
    if kind == "system":
        for st in sets:
            e = sci.brzycki_1rm(bw + (st["kg"] or 0), st["reps"] or 0)
            if e and (best is None or e > best):
                best = e
    volume = sum((st["reps"] or 0) * (st["kg"] or 0) for st in sets)
    hold = sum((st["sec"] or 0) * (st["kg"] or 0) for st in sets)
    return {"sets": len(sets), "reps": reps, "sec": secs, "volume": round(volume, 1), "hold": round(hold, 1),
            "e1rm": round(best, 1) if best else None, "e1rm_added": round(best - bw, 1) if best else None,
            "best_kg": max((st["kg"] or 0) for st in sets) if sets else 0}


def plan_for_strength_day(n):
    """Exercises of strength day n (1-3) in the plan, whatever weekday you do it on."""
    for k in range(1, 8):
        pl = plan_for(date(2024, 1, k))  # 1 January 2024 was a Monday
        if "strength" in pl["types"] and pl["day"] == n:
            return pl
    return None


def strength_day(d, strength_day_n=None):
    """Strength log of day d. The exercises come from the given strength day, from the one of the
    strength workout confirmed that day (even if done on another weekday)
    or from the day's plan."""
    data = strength_load()
    pl = plan_for(d)
    if strength_day_n is None:
        with closing(connect()) as con:
            day = compute_day(con, d, profile(con, d))
        strength_day_n = next(((t.get("label") or {}).get("day") for t in day["trainings"]
                          if (t.get("label") or {}).get("type") == "strength" and (t.get("label") or {}).get("day")), None)
    if strength_day_n in (1, 2, 3):
        pl = plan_for_strength_day(strength_day_n) or pl
    with closing(connect()) as con:
        bw = body_weight_on(con, d)
        hist = {}
        for day_s in sorted(data):
            x = date.fromisoformat(day_s)
            for e in data[day_s]["entries"]:
                kind = exercise_kind(e["name"])
                # "sets" is the number of sets; "list" the sets one by one (to prefill the next time)
                hist.setdefault(e["name"], []).append(
                    {"date": day_s, "kind": kind, "list": e["sets"], **session_metrics(kind, e["sets"], body_weight_on(con, x))})
    exercises = []
    names = [x[0] for x in pl["exercises"]]
    for name, detail in pl["exercises"]:
        kind = exercise_kind(name)
        if kind == "skip":
            continue
        last = next((h for h in reversed(hist.get(name, [])) if h["date"] < d.isoformat()), None)
        exercises.append({"name": name, "detail": detail, "kind": kind, "default_kg": default_kg(name),
                          "last": {"date": last["date"], "sets": last["list"]} if last else None})
    # exercises logged that day but no longer in the plan
    rec = data.get(d.isoformat(), {})
    saved = rec.get("entries", [])
    for e in saved:
        if e["name"] not in names:
            exercises.append({"name": e["name"], "detail": "", "kind": exercise_kind(e["name"]),
                              "default_kg": default_kg(e["name"]), "last": None})
    # session load: the saved one, the confirmed workout's one, the kg of sets saved before
    # session loads existed, or the plan's one
    confirmed = [lb.get("load_kg") for lb in strength_labels_on(d) if lb.get("load_kg")]
    if rec.get("load_kg") is not None:
        load, load_src = rec["load_kg"], "log"
    elif confirmed:
        load, load_src = confirmed[0], "allenamento"
    elif saved:
        load, load_src = max((st["kg"] or 0) for e in saved for st in e["sets"]), "log"
    else:
        load, load_src = session_load(pl), "plan"
    week = [k for k in data if d - timedelta(days=6) <= date.fromisoformat(k) <= d]
    week_sets = [st for k in week for e in data[k]["entries"] for st in e["sets"]]
    return {"date": d.isoformat(), "plan": pl["text"], "plan_day": pl["day"], "body_weight": bw,
            "load_kg": load, "load_src": load_src,
            "strength_day_numbers": [n for n in (1, 2, 3) if plan_for_strength_day(n)],
            "exercises": exercises, "entries": saved,
            "progress": [{"name": n, "kind": h[-1]["kind"], "sessions": h[-12:]} for n, h in sorted(hist.items())],
            "week": {"days": len(week), "sets": len(week_sets),
                     "volume": round(sum((st["reps"] or 0) * (st["kg"] or 0) for st in week_sets), 1),
                     "hold": round(sum((st["sec"] or 0) * (st["kg"] or 0) for st in week_sets), 1)},
            "sources": src("reynolds", "who", "momma")}


# ---------------------------------------------------------------- plan editable in the app
def validate_plan(obj):
    if not isinstance(obj, dict):
        raise UserError("e.plan_invalid")
    out = {}
    for k in range(1, 8):
        v = obj.get(str(k), {})
        if isinstance(v, str):
            v = {"text": v}
        if not isinstance(v, dict):
            raise UserError("e.plan_day", day=k)
        text = v.get("text", "")
        if not isinstance(text, str) or len(text) > 120:
            raise UserError("e.plan_text_long", day=k, max=120)
        types = [code(t) for t in v.get("types", [])] if isinstance(v.get("types", []), list) else None
        if not isinstance(types, list) or len(types) > 4 or any(t not in ACTIVITY_TYPES for t in types):
            raise UserError("e.plan_types", day=k)
        day = v.get("day")
        if day is not None and (isinstance(day, bool) or day not in (1, 2, 3)):
            raise UserError("e.plan_strength_day", day=k)
        load = v.get("load_kg")
        if load is not None and valid_load(load) is None:
            raise UserError("e.plan_load", day=k, max=LOAD_MAX)
        exs = v.get("exercises", [])
        if not isinstance(exs, list) or len(exs) > 20:
            raise UserError("e.plan_exercises", day=k, max=20)
        clean_ex = []
        for ex in exs:
            if not isinstance(ex, (list, tuple)) or not 1 <= len(ex) <= 2 or not all(isinstance(x, str) for x in ex):
                raise UserError("e.plan_exercise", day=k)
            name, detail = ex[0].strip(), (ex[1].strip() if len(ex) > 1 else "")
            if not name or len(name) > 60 or len(detail) > 80:
                raise UserError("e.plan_exercise_long", day=k)
            clean_ex.append([name, detail])
        out[str(k)] = {"text": text.strip(), "types": list(dict.fromkeys(types)),
                       "day": day if "strength" in types else None, "exercises": clean_ex}
        if load is not None and "strength" in types:
            out[str(k)]["load_kg"] = valid_load(load)  # session load (applies to every exercise)
    return out


def plan_payload():
    raw = load_plan()
    src_ = "app" if state_load("plan.json", None) else ("file" if raw else "vuoto")
    days = {}
    for k in range(1, 8):
        # same reading as plan_for, without depending on the date
        d = date(2024, 1, k)  # 1 January 2024 was a Monday
        pl = plan_for(d)
        days[str(k)] = {"text": pl["text"], "types": pl["types"], "day": pl["day"], "exercises": pl["exercises"]}
        if pl["load_kg"] is not None:
            days[str(k)]["load_kg"] = pl["load_kg"]
    return {"plan": days, "source": src_, "templates": PLAN_TEMPLATES,
            "types": [{"key": k, "name": v[0]} for k, v in ACTIVITY_TYPES.items()]}


# Plan templates the user can choose (none is active by itself). Only the WHO 2020 guidelines for
# adults: 150-300 minutes a week of moderate aerobic activity (or 75-150 vigorous) and muscle-strengthening
# activity for all major muscle groups on at least 2 days a week. The user chooses the exercises.
def _who_day(code, types=(), day=None, exercises=()):
    return {"text": M(f"tpl.who2020.{code}"), "types": list(types), "day": day,
            "exercises": [[M(f"tpl.who2020.ex.{e}"), M("tpl.who2020.ex.detail")] for e in exercises]}


_WHO_STRENGTH = ("legs", "back", "chest", "arms", "core")
PLAN_TEMPLATES = [{
    "id": "who2020",
    "days": {"1": _who_day("aerobic30", ["walk"]), "2": _who_day("strength", ["strength"], 1, _WHO_STRENGTH),
             "3": _who_day("aerobic30", ["walk"]), "4": _who_day("aerobic30", ["walk"]),
             "5": _who_day("strength", ["strength"], 2, _WHO_STRENGTH), "6": _who_day("aerobic60", ["walk"]),
             "7": _who_day("rest")},
    "sources": src("who"),
}]


def save_plan(body):
    if body.get("reset"):
        state_delete("plan.json")
        return {"ok": True}
    state_save("plan.json", validate_plan(body.get("plan")))
    return {"ok": True}


# ---------------------------------------------------------------- weight
def save_weight(day, kg):
    if day > now().date():
        raise UserError("e.date_future")
    if day.year < 2000:
        raise UserError("e.date_invalid")
    if kg is not None and (isinstance(kg, bool) or not isinstance(kg, (int, float)) or not 30 <= kg <= 250 or kg != kg):
        raise UserError("e.weight_range", lo=30, hi=250)
    with _labels_lock:
        data = weights_load()
        if kg is None:
            data.pop(day.isoformat(), None)
        else:
            data[day.isoformat()] = round(float(kg), 1)
        state_save("weights.json", data)
    return {"ok": True}


def weight_payload():
    data = weights_load()
    with closing(connect()) as con:
        p = profile(con)
    return {"entries": sorted(([k, v] for k, v in data.items()), reverse=True),
            "current": {"kg": p["weight"], "src": p["weight_src"], "date": p.get("weight_date")}}


# ---------------------------------------------------------------- nutrition
# Foods with the label values per 100 g, recipes (raw ingredients and cooked dish weight), saved meals
# and a diary by meal. Everything stays in the data folder: no external database.
# Every diary entry keeps a copy of the values: correcting a food does not change the past unless you ask.
MEALS = ("breakfast", "lunch", "dinner", "snacks")  # codes stored in the diary; names in the catalogs (meal.<code>)
NUTRIENT_LABELS = {k: M(f"nutr.{k}") for k in ("kcal", "protein", "carbs", "fat", "fiber", "sat", "salt", "polyols", "alcohol")}
FOOD_REQUIRED = ("protein", "carbs", "fat", "fiber")
FOOD_OPTIONAL = ("sat", "salt", "polyols", "alcohol")
INTAKE_KEYS = ("kcal",) + sci.MACROS
FOODS_MAX, MEALS_MAX, DAY_ENTRIES_MAX, ITEMS_MAX, PORTIONS_MAX = 600, 80, 80, 40, 8
GOALS = ("lose_fat", "maintain")  # codes (lose fat, maintain weight); names in the catalogs (goal.<code>)
BALANCE_WORN_MIN = 1200   # minutes on the wrist for a day's expenditure to enter the balance (20 hours out of 24)
NUT_WEEK_NEED = 3         # complete days in the last 7 for the weekly analysis
WEIGHT_CHECK = {"weighins": 4, "span": 21, "days": 21}  # weigh-ins, days between first and last, complete days out of 28
FUEL_TYPES = ("run", "rope", "swim", "hike", "strength")  # workouts that use glycogen
RECOVERY_H = 8            # Craven 2021: short recovery between two sessions (8 hours or less)
DEFICIT_MAX = 500         # Murphy & Koehler 2022: beyond about 500 kcal a day lean mass no longer grows
CHECK_ORDER = ("energy", "protein", "fiber", "fat", "sat", "salt", "carbs")


def check_num(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise UserError("e.value_range", field=what, lo=lo, hi=hi)
    return round(float(v), 2)


def clean_name(v, what=None, maxlen=60):
    what = what or M("f.name")
    if not isinstance(v, str) or not 0 < len(v.strip()) <= maxlen:
        raise UserError("e.text_len", field=what, max=maxlen)
    return " ".join(v.split())


def clean_per100(raw, fiber_optional=False):
    """Values per 100 g: kcal, protein, carbohydrates, fat and fibre required (kcal, if missing, are computed with
    the EU factors); saturated fat, salt, polyols and alcohol optional. fiber_optional: copy of a community food
    whose source has no fibre value (it stays missing, it is not 0)."""
    if not isinstance(raw, dict):
        raise UserError("e.nutrients_invalid")
    out = {k: check_num(raw.get(k), 0, 100, NUTRIENT_LABELS[k]) for k in FOOD_REQUIRED
           if not (k == "fiber" and fiber_optional and raw.get(k) in (None, ""))}
    for k in FOOD_OPTIONAL:
        if raw.get(k) not in (None, ""):
            out[k] = check_num(raw.get(k), 0, 100, NUTRIENT_LABELS[k])
    if sum(out.get(k, 0) for k in FOOD_REQUIRED) + out.get("alcohol", 0) > 101:
        raise UserError("e.over_100g")
    if out.get("sat", 0) > out["fat"] + 0.1:
        raise UserError("e.sat_over_fat")
    if out.get("polyols", 0) > out["carbs"] + 0.1:
        raise UserError("e.polyols_over_carbs")
    kcal = raw.get("kcal")
    out["kcal"] = check_num(kcal, 0, 900, M("f.kcal")) if kcal not in (None, "") else round(sci.atwater_kcal(out), 1)
    return out


def clean_origin(raw):
    """Where a copy of a community food comes from: source, id, catalog version and the values it had then
    (each value of the copy shows its source: the catalog while unchanged, the user once corrected)."""
    if not isinstance(raw, dict) or raw.get("src") not in hc.SOURCE_INFO:
        return None
    ref, version, v = raw.get("ref"), raw.get("version"), raw.get("v")
    if not isinstance(ref, str) or not 0 < len(ref) <= 40 or not isinstance(version, str) or len(version) > 10:
        return None
    if not isinstance(v, dict):
        return None
    vals = {k: round(float(x), 2) for k, x in v.items()
            if k in hc.NUTRIENTS and isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 900}
    return {"src": raw["src"], "ref": ref, "version": version, "v": vals}


def value_sources(f):
    """Source of each value of a food: "user", or the catalog source of a copy while the value is unchanged."""
    o = f.get("origin")
    out = {}
    for k, x in f["per100"].items():
        ov = o["v"].get(k) if o else None
        out[k] = o["src"] if ov is not None and abs(ov - x) < 0.005 else "user"
    return out


def clean_portions(raw):
    out = []
    for p_ in raw or []:
        if not isinstance(p_, (list, tuple)) or len(p_) != 2:
            raise UserError("e.portion_invalid")
        out.append([clean_name(p_[0], M("f.portion_name"), 30), check_num(p_[1], 0.1, 2000, M("f.portion_grams"))])
    if len(out) > PORTIONS_MAX:
        raise UserError("e.portions_max", max=PORTIONS_MAX)
    return out


def clean_items(raw, foods, allow_recipes):
    """Ingredients of a recipe or items of a saved meal: [id, grams]."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= ITEMS_MAX:
        raise UserError("e.items_count", max=ITEMS_MAX)
    out = []
    for it in raw:
        if not isinstance(it, (list, tuple)) or len(it) != 2 or not isinstance(it[0], str) or it[0] not in foods:
            raise UserError("e.food_not_found")
        if foods[it[0]]["kind"] != "food" and not allow_recipes:
            raise UserError("e.recipe_in_recipe")
        out.append([it[0], check_num(it[1], 0.1, 10000, M("f.grams"))])
    return out


def foods_load():
    """Foods, recipes and saved meals from the file: entries damaged or edited by hand in an invalid way are skipped."""
    data = state_load("foods.json")
    raw_f = data.get("foods") if isinstance(data.get("foods"), dict) else {}
    raw_m = data.get("meals") if isinstance(data.get("meals"), dict) else {}
    foods, meals = {}, {}
    for kind in ("food", "recipe"):
        for k, f in raw_f.items():
            if not isinstance(f, dict) or f.get("kind") != kind:
                continue
            try:
                rec = {"id": k, "kind": kind, "name": clean_name(f.get("name")), "portions": clean_portions(f.get("portions")),
                       "updated": f.get("updated") if isinstance(f.get("updated"), int) else 0}
                if kind == "food":
                    origin = clean_origin(f.get("origin"))
                    rec["per100"] = clean_per100(f.get("per100"), fiber_optional=bool(origin) and "fiber" not in origin["v"])
                    if origin:
                        rec["origin"] = origin
                    if hc.clean_barcode(f.get("barcode")):
                        rec["barcode"] = hc.clean_barcode(f.get("barcode"))
                else:
                    rec["items"] = clean_items([i for i in f.get("items") or [] if isinstance(i, list) and i and i[0] in foods],
                                               foods, False)
                    rec["cooked_g"] = check_num(f.get("cooked_g"), 1, 20000, M("f.cooked_g"))
            except (ValueError, TypeError):
                continue
            foods[k] = rec
    for k, m in raw_m.items():
        try:
            meals[k] = {"id": k, "name": clean_name(m.get("name")),
                        "meal": m.get("meal") if m.get("meal") in MEALS else None,
                        "items": clean_items([i for i in m.get("items") or [] if isinstance(i, list) and i and i[0] in foods],
                                             foods, True)}
        except (ValueError, TypeError, AttributeError):
            continue
    return foods, meals


def foods_save(foods, meals):
    state_save("foods.json", {"foods": foods, "meals": meals})


def foods_add(rec):
    """Adds a new food or recipe to foods.json leaving every other entry exactly as it is in the file (also the
    entries this version cannot read): nothing is rewritten, merged or removed."""
    data = state_load("foods.json")
    raw = data.get("foods") if isinstance(data.get("foods"), dict) else {}
    if rec["id"] in raw:
        raise UserError("e.food_exists", name=rec["name"])
    raw[rec["id"]] = rec
    data["foods"] = raw
    state_save("foods.json", data)


def new_id(prefix):
    return prefix + secrets.token_hex(5)


def food_values(f, foods):
    """Values per 100 g of a food or recipe with minimum and maximum (EU tolerances).

    Recipe: sum of the raw ingredients divided by the cooked dish weight (water that evaporates or that pasta
    absorbs changes the concentration, not the total). Saturated fat and salt stay only if every ingredient gives them.
    """
    if f["kind"] == "food":
        lo, hi = sci.food_range(f["per100"])
        return dict(f["per100"]), lo, hi
    tot, tlo, thi, missing = {}, {}, {}, set()
    for fid, g in f["items"]:
        ing = foods.get(fid)
        if not ing:
            continue
        v = ing["per100"]
        lo, hi = sci.food_range(v)
        for k in INTAKE_KEYS:
            if v.get(k) is None:
                if k in hc.SHOWN_OPTIONAL:
                    missing.add(k)
                continue
            tot[k] = tot.get(k, 0.0) + v[k] * g / 100
            tlo[k] = tlo.get(k, 0.0) + lo[k] * g / 100
            thi[k] = thi.get(k, 0.0) + hi[k] * g / 100
    scale = 100 / f["cooked_g"]
    keep = [k for k in tot if k not in missing]
    return ({k: tot[k] * scale for k in keep}, {k: tlo[k] * scale for k in keep}, {k: thi[k] * scale for k in keep})


def snapshot(f, foods):
    """Copy of a food's values for the diary."""
    v, lo, hi = food_values(f, foods)
    r = lambda dd: {k: round(x, 3) for k, x in dd.items()}
    return {"food": f["id"], "name": f["name"], "kind": f["kind"], "v": r(v), "lo": r(lo), "hi": r(hi)}


def save_food(body):
    """Creates or edits a food or recipe; {"delete": id} deletes it if no recipe or saved meal uses it.
    apply_past: also updates the entries already logged in the diary (including those of recipes containing it)."""
    if body.get("delete") is not None:
        return delete_food(body["delete"])
    raw = body.get("food")
    if not isinstance(raw, dict):
        raise UserError("e.food_invalid")
    kind = code(raw.get("kind", "food"))
    if kind not in ("food", "recipe"):
        raise UserError("err.type_invalid")
    with _labels_lock:
        foods, meals = foods_load()
        fid = raw.get("id")
        if fid is not None and (not isinstance(fid, str) or fid not in foods):
            raise UserError("e.food_not_found")
        if fid and foods[fid]["kind"] != kind:
            raise UserError("e.food_to_recipe")
        name = clean_name(raw.get("name"))
        if any(f["name"].lower() == name.lower() and k != fid for k, f in foods.items()):
            raise UserError("e.food_exists", name=name)
        if not fid and len(foods) >= FOODS_MAX:
            raise UserError("e.foods_max", max=FOODS_MAX)
        rec = {"id": fid or new_id("f" if kind == "food" else "r"), "kind": kind, "name": name,
               "portions": clean_portions(raw.get("portions")), "updated": int(time.time())}
        if kind == "food":
            # copy of a community food ("Save to my foods"): the origin is taken from the catalog, not from the page
            origin = foods[fid].get("origin") if fid else catalog_origin(raw.get("origin"))
            rec["per100"] = clean_per100(raw.get("per100"), fiber_optional=bool(origin) and "fiber" not in origin["v"])
            if origin:
                rec["origin"] = origin
            barcode = raw.get("barcode") if "barcode" in raw else (foods[fid].get("barcode") if fid else None)
            if barcode not in (None, ""):
                if not hc.clean_barcode(barcode):
                    raise UserError("e.barcode_invalid")
                rec["barcode"] = hc.clean_barcode(barcode)
        else:
            rec["items"] = clean_items(raw.get("items"), foods, False)
            cooked = raw.get("cooked_g")
            rec["cooked_g"] = (check_num(cooked, 1, 20000, M("f.cooked_g")) if cooked not in (None, "")
                               else round(sum(g for _, g in rec["items"]), 2))
        if fid:
            foods[rec["id"]] = rec
            foods_save(foods, meals)
        else:
            foods_add(rec)
            foods[rec["id"]] = rec
        updated = apply_to_diary(rec["id"], foods) if body.get("apply_past") is True and fid else 0
    return {"ok": True, "id": rec["id"], "updated": updated}


def delete_food(fid):
    with _labels_lock:
        foods, meals = foods_load()
        if not isinstance(fid, str) or fid not in foods:
            raise UserError("e.food_not_found")
        users = [f["name"] for f in foods.values() if f["kind"] == "recipe" and any(i[0] == fid for i in f["items"])]
        users += [m["name"] for m in meals.values() if any(i[0] == fid for i in m["items"])]
        if users:
            raise UserError("e.food_in_use", names=sorted(users))
        foods.pop(fid)
        foods_save(foods, meals)
    return {"ok": True}


def save_meal(body):
    """Saved meal: several foods or recipes added to the diary with one tap."""
    with _labels_lock:
        foods, meals = foods_load()
        if body.get("delete") is not None:
            if body["delete"] not in meals:
                raise UserError("e.meal_not_found")
            meals.pop(body["delete"])
            foods_save(foods, meals)
            return {"ok": True}
        raw = body.get("meal")
        if not isinstance(raw, dict):
            raise UserError("e.meal_invalid")
        mid = raw.get("id")
        if mid is not None and (not isinstance(mid, str) or mid not in meals):
            raise UserError("e.meal_not_found")
        name = clean_name(raw.get("name"))
        if any(m["name"].lower() == name.lower() and k != mid for k, m in meals.items()):
            raise UserError("e.meal_exists", name=name)
        if not mid and len(meals) >= MEALS_MAX:
            raise UserError("e.meals_max", max=MEALS_MAX)
        slot = code(raw.get("meal"))
        if slot is not None and slot not in MEALS:
            raise UserError("e.meal_slot_invalid")
        rec = {"id": mid or new_id("m"), "name": name, "meal": slot,
               "items": clean_items(raw.get("items"), foods, True), "updated": int(time.time())}
        meals[rec["id"]] = rec
        foods_save(foods, meals)
    return {"ok": True, "id": rec["id"]}


DIARY_FILE = re.compile(r"^diary-\d{4}-\d{2}\.json$")


def diary_name(d):
    return f"diary-{d.year:04d}-{d.month:02d}.json"


ENTRY_REQUIRED = ("kcal", "protein", "carbs", "fat")  # fibre can be missing in entries of community foods


def entry_ok(e):
    return (isinstance(e, dict) and isinstance(e.get("id"), str) and e.get("meal") in MEALS
            and isinstance(e.get("name"), str) and isinstance(e.get("v"), dict) and isinstance(e.get("lo"), dict)
            and isinstance(e.get("hi"), dict) and isinstance(e.get("g"), (int, float)) and not isinstance(e.get("g"), bool)
            and e["g"] > 0 and all(isinstance(e["v"].get(k), (int, float)) for k in ENTRY_REQUIRED))


def catalog_snapshot(item):
    """Diary copy of a community food (not saved among the user's foods): values, range and source."""
    v = {k: float(x) for k, x in item["per100"].items() if k in INTAKE_KEYS}
    lo, hi = sci.food_range(v)
    r = lambda dd: {k: round(x, 3) for k, x in dd.items()}
    name = item["name"] + (f" ({item['brand']})" if item.get("brand") else "")
    return {"food": None, "name": name[:80], "kind": "food", "v": r(v), "lo": r(lo), "hi": r(hi),
            "src": {"src": item["src"], "ref": item["ref"], "version": item["version"]}}


def diary_month(d):
    data = state_load(diary_name(d))
    return {k: v for k, v in data.items() if isinstance(k, str) and DATE_RE.match(k) and isinstance(v, dict)}


def diary_days(d0, d1):
    """Diary from day d0 to day d1: {date: {"entries": [...], "complete": True/False}}."""
    out, months, x = {}, {}, d0
    while x <= d1:
        key = (x.year, x.month)
        if key not in months:
            months[key] = diary_month(x)
        rec = months[key].get(x.isoformat()) or {}
        out[x.isoformat()] = {"entries": [e for e in rec.get("entries") or [] if entry_ok(e)],
                              "complete": rec.get("complete") is True}
        x += timedelta(days=1)
    return out


def diary_op(body):
    """Diary: add (food or recipe with grams), add_meal (saved meal), update (grams or meal), remove, complete."""
    day = parse_iso(body.get("date"))
    if day > now().date():
        raise UserError("e.future_meal")
    if day.year < 2000:
        raise UserError("e.date_invalid")
    op = body.get("op")
    with _labels_lock:
        month = diary_month(day)
        rec = month.get(day.isoformat()) or {}
        entries = [e for e in rec.get("entries") or [] if entry_ok(e)]
        complete = rec.get("complete") is True
        if op in ("add", "add_meal"):
            meal = code(body.get("meal"))
            if meal not in MEALS:
                raise UserError("e.meal_slot_invalid")
            foods, meals = foods_load()
            if op == "add" and body.get("catalog") is not None:
                c = body["catalog"]
                item = (catalog_get(c.get("src"), c.get("ref"), body.get("lang"))
                        if isinstance(c, dict) and isinstance(c.get("ref"), str) else None)
                if not item:
                    raise UserError("e.food_not_found")
                if len(entries) + 1 > DAY_ENTRIES_MAX:
                    raise UserError("e.entries_max", max=DAY_ENTRIES_MAX)
                entries.append({"id": new_id("e"), "meal": meal, "g": check_num(body.get("g"), 0.1, 5000, M("f.grams")),
                                **catalog_snapshot(item), "added": int(time.time())})
                items = []
            elif op == "add":
                f = foods.get(body.get("food")) if isinstance(body.get("food"), str) else None
                if not f:
                    raise UserError("e.food_not_found")
                items = [(f, check_num(body.get("g"), 0.1, 5000, M("f.grams")))]
            else:
                m = meals.get(body.get("template")) if isinstance(body.get("template"), str) else None
                if not m:
                    raise UserError("e.meal_not_found")
                items = [(foods[fid], g) for fid, g in m["items"]]
            if len(entries) + len(items) > DAY_ENTRIES_MAX:
                raise UserError("e.entries_max", max=DAY_ENTRIES_MAX)
            for f, g in items:
                entries.append({"id": new_id("e"), "meal": meal, "g": g, **snapshot(f, foods), "added": int(time.time())})
        elif op == "update":
            e = next((x for x in entries if x["id"] == body.get("id")), None)
            if not e:
                raise UserError("e.entry_not_found")
            e["g"] = check_num(body.get("g"), 0.1, 5000, M("f.grams"))
            if body.get("meal") is not None:
                if code(body["meal"]) not in MEALS:
                    raise UserError("e.meal_slot_invalid")
                e["meal"] = code(body["meal"])
        elif op == "remove":
            n = len(entries)
            entries = [x for x in entries if x["id"] != body.get("id")]
            if len(entries) == n:
                raise UserError("e.entry_not_found")
        elif op == "complete":
            if not isinstance(body.get("complete"), bool):
                raise UserError("e.value_invalid")
            complete = body["complete"]
        else:
            raise UserError("e.op_invalid")
        if entries or complete:
            month[day.isoformat()] = {"entries": entries, "complete": complete, "saved": int(time.time())}
        else:
            month.pop(day.isoformat(), None)
        if month:
            state_save(diary_name(day), month)
        else:
            state_delete(diary_name(day))
    return {"ok": True}


def apply_to_diary(fid, foods):
    """Updates the copies of the values in the diary for a corrected food and for the recipes containing it
    (call it with the lock held). Returns the number of entries changed."""
    affected = {fid} | {k for k, f in foods.items() if f["kind"] == "recipe" and any(i[0] == fid for i in f["items"])}
    fresh = {k: snapshot(foods[k], foods) for k in affected if k in foods}
    changed = 0
    for path in sorted(STATE_DIR.glob("diary-*.json")) if STATE_DIR.is_dir() else []:
        if not DIARY_FILE.match(path.name):
            continue
        data = state_load(path.name)
        dirty = False
        for rec in data.values():
            for e in rec.get("entries") or [] if isinstance(rec, dict) else []:
                if isinstance(e, dict) and e.get("food") in fresh:
                    e.update(fresh[e["food"]])
                    changed += 1
                    dirty = True
        if dirty:
            state_save(path.name, data)
    return changed


def nutrition_settings():
    s = state_load("nutrition.json")
    return {"goal": s.get("goal") if s.get("goal") in GOALS else "lose_fat"}


def save_goal(body):
    goal = code(body.get("goal"))
    if goal not in GOALS:
        raise UserError("e.goal_invalid")
    with _labels_lock:
        s = state_load("nutrition.json")
        s["goal"] = goal
        state_save("nutrition.json", s)
    return {"ok": True}


def foods_payload():
    foods, meals = foods_load()
    used = {}
    for f in foods.values():
        for fid, _ in f.get("items", []):
            used.setdefault(fid, set()).add(f["name"])
    for m in meals.values():
        for fid, _ in m["items"]:
            used.setdefault(fid, set()).add(m["name"])
    r = lambda dd: {k: round(x, 2) for k, x in dd.items()}
    out = []
    for f in sorted(foods.values(), key=lambda x: x["name"].lower()):
        v, lo, hi = food_values(f, foods)
        it = {"id": f["id"], "name": f["name"], "kind": f["kind"], "per100": r(v), "portions": f["portions"],
              "missing": [k for k in hc.SHOWN_OPTIONAL if v.get(k) is None], "used_in": sorted(used.get(f["id"], ()))}
        if f["kind"] == "food":
            it["check"] = sci.label_energy_check(f["per100"])
            it["src"] = value_sources(f)
            if f.get("origin"):
                it["origin"] = {"src": f["origin"]["src"], "ref": f["origin"]["ref"], "version": f["origin"]["version"]}
            if f.get("barcode"):
                it["barcode"] = f["barcode"]
        else:
            it["items"] = [{"food": i, "name": foods[i]["name"], "g": g} for i, g in f["items"]]
            it["cooked_g"], it["raw_g"] = f["cooked_g"], round(sum(g for _, g in f["items"]), 1)
        out.append(it)
    meal_list = []
    for m in sorted(meals.values(), key=lambda x: x["name"].lower()):
        kcal = sum(food_values(foods[i], foods)[0]["kcal"] * g / 100 for i, g in m["items"])
        meal_list.append({"id": m["id"], "name": m["name"], "meal": m["meal"], "kcal": round(kcal),
                          "items": [{"food": i, "name": foods[i]["name"], "kind": foods[i]["kind"], "g": g} for i, g in m["items"]]})
    return {"foods": out, "meals": meal_list, "goal": nutrition_settings()["goal"],
            "goals": [{"key": k, "name": M(f"goal.{k}")} for k in GOALS],
            "meal_names": [{"key": k, "name": M(f"meal.{k}")} for k in MEALS],
            "factors": sci.ATWATER, "sources": src("eu_kcal", "eu_tol")}


def entry_source(e):
    """Source of a diary entry: a community catalog (entries added without saving the food) or the user's foods."""
    s = e.get("src")
    if isinstance(s, dict) and s.get("src") in hc.SOURCE_INFO:
        return {"src": s["src"], "version": s.get("version")}
    return {"src": "user"}


# ------------------------------------------------ food catalog (community databases, read only)
# Search, barcodes and items read the catalog files in CATALOG_DIR and never the network: a new package is
# installed only from a file (import on the phone, the command line on the server); nothing is ever downloaded.
# Installing, updating or removing a catalog never touches STATE_DIR: the user's foods, recipes, saved meals
# and diary stay as they are.
# left by versions up to 266, which had "Update from URL": removed on first use
OLD_URL_FILE = "source.json"
_catalog_lock = threading.Lock()
_catalog_checked = False


def catalog_ready():
    """Installs the catalog shipped with the app on first use (and after an update of the app)."""
    global _catalog_checked
    if _catalog_checked:
        return
    with _catalog_lock:
        if not _catalog_checked:
            try:
                hc.ensure_bundled(CATALOG_BUNDLED, CATALOG_DIR, STATE_DIR)
            except (hc.CatalogError, OSError, sqlite3.Error) as e:
                print(f"Bundled food catalog not installed: {e!r}", flush=True)
            try:
                (CATALOG_DIR / OLD_URL_FILE).unlink()
            except OSError:
                pass
            _catalog_checked = True


def catalog_lang(lang):
    return lang if lang in LANGS else (settings_load()["lang"] or "en")


def catalog_error(e):
    return UserError(e.key, **e.params)


def catalog_item(it):
    """A community food for the page, with the short name of its source."""
    return {k: it[k] for k in ("cat", "src", "ref", "name", "brand", "barcode", "countries", "country_from_gs1", "per100",
                               "missing", "portions", "version")} | {
        "short": hc.SOURCE_INFO[it["src"]]["short"]}


def catalog_get(src, ref, lang=None):
    catalog_ready()
    return hc.get(CATALOG_DIR, src, ref, catalog_lang(lang))


def catalog_origin(raw):
    """Origin of a new copy ("Save to my foods"): the catalog food named by the page, with its values as they are
    in the catalog now (the page cannot claim other values as coming from the source)."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("ref"), str):
        raise UserError("e.food_not_found")
    item = catalog_get(raw.get("src"), raw["ref"])
    if not item:
        raise UserError("e.food_not_found")
    return {"src": item["src"], "ref": item["ref"], "version": item["version"] or "", "v": dict(item["per100"])}


def mine_item(kind, x, foods):
    if kind == "meal":
        kcal = sum(food_values(foods[i], foods)[0]["kcal"] * g / 100 for i, g in x["items"])
        return {"type": "meal", "id": x["id"], "name": x["name"], "kcal": round(kcal)}
    v = food_values(x, foods)[0]
    return {"type": x["kind"], "id": x["id"], "name": x["name"], "per100": {k: round(y, 2) for k, y in v.items()},
            "portions": x["portions"], "missing": [k for k in hc.SHOWN_OPTIONAL if v.get(k) is None],
            "origin": x["origin"]["src"] if x.get("origin") else None}


def mine_search(q, limit=30):
    """The user's foods, recipes and saved meals whose name has every word searched (as the start of a word)."""
    qn = hc.norm(q)[:80]
    tokens = qn.split()
    if not tokens:
        return []
    foods, meals = foods_load()
    found = []
    for kind, x in [("food", f) for f in foods.values()] + [("meal", m) for m in meals.values()]:
        n = hc.norm(x["name"])
        words = n.split()
        if all(any(w.startswith(t) for w in words) for t in tokens):
            found.append(((n != qn, not n.startswith(qn), len(n), n), kind, x))
    found.sort(key=lambda t: t[0])
    return [mine_item(kind, x, foods) for _, kind, x in found[:limit]]


def catalog_search(q, lang=None, limit=30):
    """Search: first the user's foods, then the community catalogs (each result with its source)."""
    catalog_ready()
    q = (q or "")[:80]
    lang = catalog_lang(lang)
    return {"q": q, "mine": mine_search(q, limit),
            "community": [catalog_item(it) for it in hc.search(CATALOG_DIR, q, lang, limit)]}


def catalog_barcode(code_, lang=None):
    """Barcode: the user's foods with that code first, then the community products."""
    catalog_ready()
    key = hc.gtin_key(code_)
    if not key:
        raise UserError("e.barcode_invalid")
    foods, _ = foods_load()
    mine = [mine_item("food", f, foods) for f in foods.values() if hc.gtin_key(f.get("barcode")) == key]
    return {"code": hc.gtin_display(key), "mine": mine,
            "community": [catalog_item(it) for it in hc.by_barcode(CATALOG_DIR, code_, catalog_lang(lang))]}


def catalog_bench(lang=None):
    """Times the search on this device (Profile > Food database > Test speed): milliseconds per search."""
    catalog_ready()
    lang = catalog_lang(lang)
    out = []
    # typical searches of the language (catalog key cat.bench_queries), from short prefixes to whole names
    for q in tr(lang, "cat.bench_queries").split("|"):
        t0 = time.perf_counter()
        n = len(catalog_search(q, lang)["community"])
        out.append({"q": q, "ms": round((time.perf_counter() - t0) * 1000, 1), "n": n})
    t0 = time.perf_counter()
    hc.by_barcode(CATALOG_DIR, "20570910", lang)
    out.append({"q": "20570910", "ms": round((time.perf_counter() - t0) * 1000, 1), "n": None})
    ms = sorted(x["ms"] for x in out)
    return {"queries": out, "median_ms": ms[len(ms) // 2], "max_ms": ms[-1]}


def catalog_info():
    """Installed catalogs with their sources and licences."""
    catalog_ready()
    out = {}
    for kind, m in hc.info(CATALOG_DIR).items():
        out[kind] = {"version": m["version"], "rows": m["rows"], "file_size": m["file_size"],
                     "sqlite_sha256": m["sqlite_sha256"],
                     "sources": [{**s, **hc.SOURCE_INFO[s["id"]]} for s in m["sources"] if s.get("id") in hc.SOURCE_INFO]}
    return {"installed": out, "outdated": hc.outdated(CATALOG_DIR), "kinds": list(hc.KINDS),
            "sources": {k: v for k, v in hc.SOURCE_INFO.items()}}


def catalog_install(path):
    """Installs a package from a file (import on the phone, or the command line on the server): only the
    community catalog can be replaced this way."""
    with _catalog_lock:
        try:
            m = hc.install(path, CATALOG_DIR, STATE_DIR, kinds=("off",))
        except hc.CatalogError as e:
            raise catalog_error(e)
    return {"ok": True, "kind": m["kind"], "version": m["version"], "rows": m["rows"]}


def catalog_op(body):
    op = body.get("op")
    if op == "remove":
        if body.get("kind") != "off":
            raise UserError("cat.e.kind")
        with _catalog_lock:
            try:
                hc.remove("off", CATALOG_DIR, STATE_DIR)
            except hc.CatalogError as e:
                raise catalog_error(e)
        return {"ok": True}
    raise UserError("e.op_invalid")


# ------------------------------------------------ analysis: intake, balance, deficiencies and excesses
def intake(entries):
    """Diary total with minimum and maximum; for saturated fat and salt also the foods missing the value."""
    tot = {k: 0.0 for k in INTAKE_KEYS}
    lo, hi = dict(tot), dict(tot)
    miss = {k: [] for k in hc.SHOWN_OPTIONAL}
    miss_kcal = {k: 0.0 for k in hc.SHOWN_OPTIONAL}
    for e in entries:
        f = e["g"] / 100
        for k in INTAKE_KEYS:
            if e["v"].get(k) is None:
                if k in miss:
                    miss[k].append(e["name"])
                    miss_kcal[k] += e["v"]["kcal"] * f
                continue
            tot[k] += e["v"][k] * f
            lo[k] += e["lo"].get(k, e["v"][k]) * f
            hi[k] += e["hi"].get(k, e["v"][k]) * f
    return {"tot": tot, "lo": lo, "hi": hi, "n": len(entries),
            "missing": {k: {"names": sorted(set(miss[k])), "n": len(miss[k]), "kcal": miss_kcal[k]} for k in miss}}


def intake_mean(items):
    """Average of several days (same structure as intake)."""
    n = len(items)
    avg = lambda key: {k: sum(it[key][k] for it in items) / n for k in INTAKE_KEYS}
    return {"tot": avg("tot"), "lo": avg("lo"), "hi": avg("hi"), "n": sum(it["n"] for it in items),
            "missing": {k: {"names": sorted({x for it in items for x in it["missing"][k]["names"]}),
                            "n": sum(it["missing"][k]["n"] for it in items),
                            "kcal": sum(it["missing"][k]["kcal"] for it in items) / n} for k in hc.SHOWN_OPTIONAL}}


def ncheck(key, title, status, label, display, target, text, sources, value=None, rng=None, goal=None, direction=None):
    """One check: goal and direction ("min", "max") drive the progress bar on days in progress."""
    return {"key": key, "title": title, "status": status, "label": label, "display": display, "target": target,
            "text": text, "sources": src(*sources), "value": None if value is None else round(value, 1),
            "range": None if rng is None else [round(rng[0], 1), round(rng[1], 1)],
            "goal": None if goal is None else round(goal, 1), "dir": direction}


PROTEIN_TEXT = {(goal, ok): M(f"nc.protein.{goal}.{'ok' if ok else 'low'}")
                for goal in ("lose_fat", "maintain") for ok in (False, True)}


def protein_check(it, w, goal):
    g, lo, hi = it["tot"]["protein"], it["lo"]["protein"], it["hi"]["protein"]
    efsa = 0.83 * w
    # in a deficit Refalo 2025, when maintaining Morton 2018: both flagged for conflicts of interest
    ref_k, sources = (1.9, ["efsa_protein", "refalo"]) if goal == "lose_fat" else (1.6, ["efsa_protein", "morton_prot"])
    ref = ref_k * w
    disp = M("nc.protein.display", g=g, gkg=g / w)
    target = M("nc.protein.target", ref=ref, refk=ref_k, efsa=efsa)
    title = M("nutr.title.protein")
    st = sci.judge(g, lo, hi, minimum=efsa)
    if st != "ok":
        return ncheck("protein", title, st, M("nc.protein.below_efsa"), disp, target,
                      M("nc.protein.below_efsa_text", efsa=efsa), sources, g, (lo, hi), ref, "min")
    st = sci.judge(g, lo, hi, minimum=ref)
    return ncheck("protein", title, st, M("nc.below_ref") if st != "ok" else M("nc.in_ref"), disp, target,
                  PROTEIN_TEXT[(goal, st == "ok")], sources, g, (lo, hi), ref, "min")


def missing_text(m):
    """Which foods have no value for a nutrient (the first three)."""
    if not m["names"]:
        return None
    return M("nc.missing_foods", names=m["names"][:3], more=len(m["names"]) > 3, n=1 if len(m["names"]) <= 3 else 2)


def nutrient_checks(it, w, goal):
    """Deficiencies and excesses of a day or of the average of several days. Status: ok, caution (conditional
    recommendation), probable / certain (by the range from EU tolerances), incomplete or info (missing value or only a minimum)."""
    t, lo, hi = it["tot"], it["lo"], it["hi"]
    if t["kcal"] <= 0:
        return []
    sh = sci.energy_shares(t, lo, hi) or {}
    out = [protein_check(it, w, goal)]

    mf = it["missing"]["fiber"]
    st = sci.judge(t["fiber"], lo["fiber"], hi["fiber"], minimum=25)
    if mf["n"] and st != "ok":
        # some foods have no fibre value: the total is only a minimum, no judgement below the reference
        st, label = "info", M("nc.partial")
    else:
        label = M("nc.fiber.below") if st != "ok" else M("nc.fiber.ok")
    out.append(ncheck("fiber", M("nutr.title.fiber"), st, label,
                      M("nc.at_least", g=t["fiber"], n=mf["n"]) if mf["n"] else M("nc.grams", g=t["fiber"]),
                      M("nc.fiber.target"), J(M("nc.fiber.text"), missing_text(mf)),
                      ["who_cho", "efsa_cho"], t["fiber"], (lo["fiber"], hi["fiber"]), 25, "min"))

    if "fat" in sh:
        s = sh["fat"]
        if s["value"] > 35:
            st, label = sci.judge(s["value"], s["lo"], s["hi"], maximum=35), M("nc.above_efsa")
        elif s["value"] > 30:
            st, label = "caution", M("nc.fat.above_who")
        elif s["value"] < 20:
            st, label = sci.judge(s["value"], s["lo"], s["hi"], minimum=20), M("nc.below_efsa")
        else:
            st, label = "ok", M("nc.in_range")
        out.append(ncheck("fat", M("nutr.title.fat"), st, label, M("nc.grams_pct", g=t["fat"], pct=s["value"]),
                          M("nc.fat.target"), M("nc.fat.text"), ["efsa_fat", "who_fat"], s["value"], (s["lo"], s["hi"])))

    ms = it["missing"]["sat"]
    miss_pct = 100 * ms["kcal"] / t["kcal"] if t["kcal"] else 0
    miss_txt = (M("nc.sat.missing", names=ms["names"][:3], more=len(ms["names"]) > 3, pct=miss_pct,
                  n=1 if len(ms["names"]) <= 3 else 2) if ms["names"] else None)
    s = sh.get("sat")
    if s and miss_pct < 100:
        if not ms["names"]:
            st = sci.judge(s["value"], s["lo"], s["hi"], maximum=10)
        else:
            st = ("certain" if s["lo"] > 10 else "probable") if s["value"] > 10 else "info"
        label = {"ok": M("nc.sat.ok"), "info": M("nc.partial")}.get(st, M("nc.sat.over"))
        disp = (M("nc.at_least_pct", g=t["sat"], pct=s["value"], n=ms["n"]) if ms["names"]
                else M("nc.grams_pct_1", g=t["sat"], pct=s["value"]))
    else:
        st, label, disp = "incomplete", M("nc.no_data"), "–"
    out.append(ncheck("sat", M("nutr.title.sat"), st, label, disp, M("nc.sat.target"), J(M("nc.sat.text"), miss_txt),
                      ["who_sfa"], s["value"] if s else None, (s["lo"], s["hi"]) if s else None, 10, "max"))

    ml = it["missing"]["salt"]
    if t["salt"] > 5:
        st, label = ("certain" if lo["salt"] > 5 else "probable"), M("nc.salt.over")
    else:
        st, label = "info", M("nc.salt.logged_only")
    out.append(ncheck("salt", M("nutr.title.salt"), st, label,
                      M("nc.at_least_1", g=t["salt"], n=ml["n"]) if ml["names"] else M("nc.grams_1", g=t["salt"]),
                      M("nc.salt.target"),
                      J(M("nc.salt.text"), M("nc.salt.missing", names=ml["names"][:3], more=len(ml["names"]) > 3,
                                             n=1 if len(ml["names"]) <= 3 else 2) if ml["names"] else None),
                      ["who_salt"], t["salt"], (lo["salt"], hi["salt"]), 5, "max"))

    if "carbs" in sh:
        s = sh["carbs"]
        if s["value"] < 45:
            st, label = sci.judge(s["value"], s["lo"], s["hi"], minimum=45), M("nc.below_efsa")
        elif s["value"] > 60:
            st, label = sci.judge(s["value"], s["lo"], s["hi"], maximum=60), M("nc.above_efsa")
        else:
            st, label = "ok", M("nc.in_efsa")
        out.append(ncheck("carbs", M("nutr.title.carbs"), st, label, M("nc.grams_pct", g=t["carbs"], pct=s["value"]),
                          M("nc.carbs.target"), M("nc.carbs.text"), ["efsa_cho"], s["value"], (s["lo"], s["hi"])))
    return out


def energy_check(b, lo, hi, goal):
    """Average balance (intake - expenditure, kcal per day) with its range."""
    disp = M("nc.energy.display", sign="+" if b > 0 else "−" if b < 0 else "", v=abs(b))
    target = M(f"nc.energy.target.{goal}")
    if b < -DEFICIT_MAX:
        st, code = ("certain" if hi < -DEFICIT_MAX else "probable"), "deficit_over"
    elif goal == "lose_fat" and b > 0:
        st, code = ("certain" if lo > 0 else "probable"), "no_deficit"
    elif goal == "maintain" and lo > 0:
        st, code = "info", "surplus"
    else:
        st, code = "ok", "moderate_deficit" if goal == "lose_fat" else "balanced"
    return ncheck("energy", M("nutr.title.energy"), st, M(f"nc.energy.{code}"), disp, target,
                  M(f"nc.energy.{code}.text"), ["murphy_koehler"], b, (lo, hi))


def day_energy(x):
    """The day's energy as every screen shows it (Today, Fitness, Food and the CSV): basal metabolism of the whole
    day plus the activity; today the activity so far, so it is the least the day will cost (partial)."""
    k = x["kcal"]
    return {"kcal": k["total"], "range": k["range"], "partial": k["partial"]}


def weight_check(d, hist, diary, today):
    """Check against weight (Hall 2008): is the trend of the weigh-ins compatible with the logged balance?"""
    d0 = d - timedelta(days=27)
    weights = sorted((k, v) for k, v in weights_load().items() if d0.isoformat() <= k <= d.isoformat())
    bal = []
    for x in hist:
        rec = diary.get(x["date"])
        if not rec or not rec["complete"] or not rec["entries"] or date.fromisoformat(x["date"]) >= today:
            continue
        if not x["has_data"] or x["worn_min"] < BALANCE_WORN_MIN:
            continue
        it = intake(rec["entries"])
        bal.append((it["tot"]["kcal"] - x["kcal"]["total"], it["lo"]["kcal"] - x["kcal"]["range"][1],
                    it["hi"]["kcal"] - x["kcal"]["range"][0]))
    span = (date.fromisoformat(weights[-1][0]) - date.fromisoformat(weights[0][0])).days if weights else 0
    out = {"weighins": len(weights), "span": span, "days": len(bal), "need": WEIGHT_CHECK,
           "sources": src("hall"), "status": "collecting"}
    if len(weights) < WEIGHT_CHECK["weighins"] or span < WEIGHT_CHECK["span"] or len(bal) < WEIGHT_CHECK["days"]:
        out["text"] = M("wc.need", weighins=WEIGHT_CHECK["weighins"], span=WEIGHT_CHECK["span"], days=WEIGHT_CHECK["days"],
                        now_w=len(weights), now_d=len(bal),
                        now_span=M("wc.over_days", n=span) if len(weights) > 1 else "")
        return out
    xs = [(date.fromisoformat(k) - d0).days for k, _ in weights]
    tr = sci.linear_trend(xs, [v for _, v in weights])
    b, b_lo, b_hi = (sum(x[i] for x in bal) / len(bal) for i in range(3))
    r_lo, r_hi = sci.weight_rate_bounds(b_lo, b_hi)
    out.update({"balance": round(b), "balance_range": [round(b_lo), round(b_hi)],
                "trend_week": round(tr["slope"] * 7, 2), "trend_ci": [round(tr["lo"] * 7, 2), round(tr["hi"] * 7, 2)],
                "expected_week": [round(r_lo * 7, 2), round(r_hi * 7, 2)]})
    vals = {"slope": tr["slope"] * 7, "slo": tr["lo"] * 7, "shi": tr["hi"] * 7, "elo": r_lo * 7, "ehi": r_hi * 7}
    if tr["lo"] > r_hi:
        out["status"], code = "probable", "slower"
    elif tr["hi"] < r_lo:
        out["status"], code = "probable", "faster"
    else:
        out["status"], code = "ok", "coherent"
    out["text"] = J(M(f"wc.{code}", **vals), M("wc.fat_energy", kg=sci.min_weight_change(500 * 28)))
    return out


def recovery_pairs(trainings):
    """Two glycogen-using workouts 8 hours or less apart (Craven 2021)."""
    tr = sorted((t for t in trainings if training_type(t) in FUEL_TYPES), key=lambda t: t["start"])
    out = []
    for a, b in zip(tr, tr[1:]):
        gap = (b["start"] - a["end"]) / 3600
        if 0 < gap <= RECOVERY_H:
            out.append({"first": a["name"], "first_end": a["end"], "second": b["name"], "second_start": b["start"],
                        "gap_h": round(gap, 1)})
    return out


ACSM_LOADS = [(45, "light", (3, 5)), (90, "moderate", (5, 7)), (240, "high", (6, 10)), (None, "very_high", (8, 12))]


def athlete_reference(minutes, w, carbs_g):
    """Carbohydrate doses for athletes (ACSM, Academy of Nutrition and Dietetics, Dietitians of Canada 2016): only a
    flagged reference, it does not trigger alerts."""
    for lim, name, rng in ACSM_LOADS:
        if lim is None or minutes < lim:
            break
    return {"minutes": minutes, "category": name, "range": list(rng), "grams": [round(rng[0] * w), round(rng[1] * w)],
            "carbs_g": round(carbs_g) if carbs_g is not None else None,
            "g_per_kg": round(carbs_g / w, 1) if carbs_g is not None else None,
            "text": M("nut.athlete_text", load=M(f"load.{name}"), min=minutes, lo=rng[0], hi=rng[1],
                      glo=rng[0] * w, ghi=rng[1] * w),
            "sources": src("acsm_nutr")}


def nutrition_analysis(con, d, p, hist):
    """Nutrition of day d: diary, intake with its range, expenditure from the band, balance, deficiencies and
    excesses of the day and of the last 7 complete days, weight check, recovery between close sessions."""
    today = now().date()
    goal = nutrition_settings()["goal"]
    w = p["weight"]
    diary = diary_days(d - timedelta(days=27), d)
    by_date = {x["date"]: x for x in hist}
    rec = diary[d.isoformat()]
    it = intake(rec["entries"])
    day = by_date.get(d.isoformat())
    exp = day_energy(day) if day and d <= today else None
    r1 = lambda v: round(v, 1)
    res = {"date": d.isoformat(), "today": d == today, "goal": goal, "goal_name": M(f"goal.{goal}"), "weight": w,
           "weight_src": p.get("weight_src"), "complete": rec["complete"], "n": it["n"],
           "totals": {k: r1(v) for k, v in it["tot"].items()},
           "range": {k: [r1(it["lo"][k]), r1(it["hi"][k])] for k in INTAKE_KEYS},
           "missing": {k: {"names": v["names"], "n": v["n"],
                           "pct": round(100 * v["kcal"] / it["tot"]["kcal"]) if it["tot"]["kcal"] else 0}
                       for k, v in it["missing"].items()},
           "meals": [], "expenditure": None, "balance": None, "shares": None}
    for key in MEALS:
        es = [e for e in rec["entries"] if e["meal"] == key]
        res["meals"].append({"key": key, "name": M(f"meal.{key}"), "kcal": round(sum(e["v"]["kcal"] * e["g"] / 100 for e in es)),
                             "entries": [{"id": e["id"], "food": e.get("food"), "name": e["name"], "kind": e.get("kind"),
                                          "g": e["g"], "kcal": round(e["v"]["kcal"] * e["g"] / 100),
                                          "per100": {k: round(e["v"][k], 2) for k in ("kcal",) + FOOD_REQUIRED
                                                     if e["v"].get(k) is not None},
                                          "src": entry_source(e)}
                                         for e in es]})
    sh = sci.energy_shares(it["tot"], it["lo"], it["hi"]) if it["n"] else None
    if sh:
        res["shares"] = {k: {"value": round(v["value"]), "lo": round(v["lo"]), "hi": round(v["hi"])} for k, v in sh.items()}
    if exp:
        res["expenditure"] = {**exp, "worn_min": day["worn_min"],
                              "valid": day["has_data"] and (exp["partial"] or day["worn_min"] >= BALANCE_WORN_MIN)}
        if it["n"]:
            res["balance"] = {"kcal": round(it["tot"]["kcal"] - exp["kcal"]),
                              "range": [round(it["lo"]["kcal"] - exp["range"][1]), round(it["hi"]["kcal"] - exp["range"][0])]}
    # deficiencies and excesses of the day: final only once the day is complete (before that only salt, which can only rise)
    checks = nutrient_checks(it, w, goal) if it["n"] else []
    if not rec["complete"]:
        for c in checks:
            if not (c["key"] == "salt" and c["status"] in ("certain", "probable")):
                c["status"], c["label"] = "in_progress", M("nc.day_running")
    res["day_checks"] = checks

    # last 7 days
    week_days = [(d - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    closed = [k for k in week_days if diary[k]["complete"] and diary[k]["entries"]]
    week = {"days": len(closed), "need": NUT_WEEK_NEED, "dates": closed, "checks": [], "energy": None}
    if len(closed) >= NUT_WEEK_NEED:
        avg = intake_mean([intake(diary[k]["entries"]) for k in closed])
        week["intake"] = {k: r1(v) for k, v in avg["tot"].items()}
        week["checks"] = nutrient_checks(avg, w, goal)
    bal = []
    for k in closed:
        x = by_date.get(k)
        if x and date.fromisoformat(k) < today and x["has_data"] and x["worn_min"] >= BALANCE_WORN_MIN:
            ik = intake(diary[k]["entries"])
            bal.append((ik["tot"]["kcal"] - x["kcal"]["total"], ik["lo"]["kcal"] - x["kcal"]["range"][1],
                        ik["hi"]["kcal"] - x["kcal"]["range"][0]))
    week["energy_days"] = len(bal)
    if len(bal) >= NUT_WEEK_NEED:
        b, lo, hi = (sum(x[i] for x in bal) / len(bal) for i in range(3))
        week["energy"] = energy_check(b, lo, hi, goal)
        week["checks"].insert(0, week["energy"])
    res["week"] = week
    res["days"] = []
    for k in week_days:
        x = by_date.get(k)
        ik = intake(diary[k]["entries"]) if diary[k]["entries"] else None
        valid = bool(x and date.fromisoformat(k) < today and x["has_data"] and x["worn_min"] >= BALANCE_WORN_MIN)
        res["days"].append({"date": k, "intake": round(ik["tot"]["kcal"]) if ik else None, "complete": diary[k]["complete"],
                            "expenditure": x["kcal"]["total"] if valid else None})
    res["weight_check"] = weight_check(d, hist, diary, today)
    prev = by_date.get((d - timedelta(days=1)).isoformat())
    trs = (prev["trainings"] if prev else []) + (day["trainings"] if day else [])
    pairs = [x for x in recovery_pairs(trs) if datetime.fromtimestamp(x["second_start"], TZ).date() == d
             or datetime.fromtimestamp(x["first_end"], TZ).date() == d]
    res["recovery"] = None
    if pairs:
        res["recovery"] = {"pairs": pairs, "dose": round(1.02 * w),
                           "text": M("nut.recovery_text", g=1.02 * w),
                           "sources": src("craven")}
    minutes = sum(t["duration_min"] for t in (day["trainings"] if day else []) if training_type(t) in FUEL_TYPES)
    res["athlete"] = athlete_reference(minutes, w, it["tot"]["carbs"] if it["n"] else None)
    res["method"] = {"text": M("nut.method_text"),
                     "sources": src("eu_kcal", "eu_tol")}
    return res


def nutrition_day(d):
    with closing(connect()) as con:
        p = profile(con, d)
        hist = history(con, min(d, now().date()), 28) if d <= now().date() else []
        return nutrition_analysis(con, d, p, hist)


def nutrition_brief(n):
    """For the Today screen: the most important issue of the last 7 complete days."""
    order = {k: i for i, k in enumerate(CHECK_ORDER)}
    issues = sorted((c for c in n["week"]["checks"] if c["status"] in ("certain", "probable")),
                    key=lambda c: (c["status"] != "certain", order.get(c["key"], 99)))
    return {"days": n["week"]["days"], "logged": n["n"] > 0, "complete": n["complete"],
            "issues": [{**{k: c[k] for k in ("key", "title", "status", "label", "display", "target")},
                        "days": n["week"]["energy_days"] if c["key"] == "energy" else n["week"]["days"]} for c in issues]}


# ---------------------------------------------------------------- CSV export
EXPORT_MAX_DAYS = 731


def csv_cell(v, fmt, lang="en"):
    if v is None:
        return ""
    if isinstance(v, dict) and "$" in v:
        v = tr(lang, v["$"], **{k: x for k, x in v.items() if k != "$"})
    if isinstance(v, bool):
        return tr(lang, "csv.yes" if v else "csv.no")
    if isinstance(v, float):
        txt = f"{v:.2f}".rstrip("0").rstrip(".")
        return txt.replace(".", ",") if fmt == "eu" else txt
    if isinstance(v, int):
        return str(v)
    txt = str(v)
    if txt[:1] in ("=", "+", "-", "@", "\t", "\r"):
        txt = "'" + txt  # a spreadsheet must not run formulas coming from the data
    sep = ";" if fmt == "eu" else ","
    if any(c in txt for c in (sep, '"', "\n", "\r")):
        txt = '"' + txt.replace('"', '""') + '"'
    return txt


def csv_text(cols, rows, fmt, lang="en"):
    """cols: column codes; the header is in the chosen language (csv.col.<code>)."""
    sep = ";" if fmt == "eu" else ","
    header = [csv_cell(tr(lang, f"csv.col.{c}"), fmt) for c in cols]
    lines = [sep.join(header)] + [sep.join(csv_cell(v, fmt, lang) for v in r) for r in rows]
    return ("﻿" if fmt == "eu" else "") + "\r\n".join(lines) + "\r\n"


def hhmm(t):
    return datetime.fromtimestamp(t, TZ).strftime("%H:%M")


EXPORT_KINDS = ("days", "workouts", "strength", "food")  # days, workouts, strength, food (URL codes)


def csv_code(lang, prefix, v):
    """Code stored in the data ("band", "recipe", "strength"...) in the chosen language, if the catalog knows it."""
    key = f"{prefix}.{v}"
    return tr(lang, key) if v is not None and key in catalog("en") else v


def export_csv(kind, d0, d1, fmt="eu", lang="en"):
    if kind not in EXPORT_KINDS:
        raise UserError("e.export_kind")
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days >= EXPORT_MAX_DAYS:
        raise UserError("e.export_days", max=EXPORT_MAX_DAYS)
    days, x = [], d0
    while x <= d1:
        days.append(x)
        x += timedelta(days=1)
    if kind == "food":
        rows = []
        for day_s, rec in diary_days(d0, d1).items():
            for e in sorted(rec["entries"], key=lambda e: MEALS.index(e["meal"])):
                f = e["g"] / 100
                val = lambda k: round(e["v"][k] * f, 2) if e["v"].get(k) is not None else None
                rows.append([day_s, M(f"meal.{e['meal']}"), e["name"], csv_code(lang, "csv.v", e.get("kind", "food")),
                             e["g"], val("kcal"),
                             val("protein"), val("carbs"), val("fat"), val("fiber"), val("sat"), val("salt"), rec["complete"]])
        cols = ["date", "meal", "food", "kind", "grams", "kcal", "protein_g", "carbs_g", "fat_g", "fiber_g",
                "sat_g", "salt_g", "day_complete"]
        return csv_text(cols, rows, fmt, lang)
    if kind == "strength":
        data = strength_load()
        rows = []
        with closing(connect()) as con:
            for day_s in sorted(k for k in data if d0.isoformat() <= k <= d1.isoformat()):
                bw = body_weight_on(con, date.fromisoformat(day_s))
                for e in data[day_s]["entries"]:
                    kind_e = exercise_kind(e["name"])
                    for i, st in enumerate(e["sets"], 1):
                        e1 = sci.brzycki_1rm(bw + (st["kg"] or 0), st["reps"] or 0) if kind_e == "system" else None
                        rows.append([day_s, e["name"], i, st["reps"], st["kg"], st["sec"], round(e1, 1) if e1 else None])
        cols = ["date", "exercise", "set", "reps", "added_kg", "seconds", "e1rm_system_kg"]
        return csv_text(cols, rows, fmt, lang)
    rows = []
    with closing(connect()) as con:
        for x in days:
            p = profile(con, x)
            day = compute_day(con, x, p)
            if kind == "days":
                sl = sleep_for(con, x)
                rows.append([x.isoformat(), day["steps"], day["jumps"], day["distance_km"], day["active_min"],
                             day["kcal"]["total"], day["kcal"]["active"], day["kcal"]["range"][0], day["kcal"]["range"][1],
                             day["kcal"]["bmr"], day["resting"], csv_code(lang, "csv.v", day["resting_src"]),
                             sl["hrv"] if sl else None, sl["total"] if sl else None, sl["deep"] if sl else None,
                             sl["rem"] if sl else None, sl["awake"] if sl else None, sl["score"] if sl else None,
                             sl["spo2"] if sl else None, sl["resp"] if sl else None, sl["temp"] if sl else None,
                             day["trimp"], day["zone_minutes"], len(day["trainings"]), p["weight"], p["vo2max_est"]])
            else:
                for t in day["trainings"]:
                    rows.append([x.isoformat(), hhmm(t["start"]), hhmm(t["end"]), csv_code(lang, "act", training_type(t)),
                                 t["name"], bool(t.get("label")), csv_code(lang, "csv.v", t["source"]), t["duration_min"],
                                 t["steps"], t["jumps"],
                                 t["avg_hr"], t["max_hr"], t["kcal"], t["kcal_range"][0], t["kcal_range"][1],
                                 t["kcal_method"], t["trimp"], (t.get("label") or {}).get("load_kg")])
    if kind == "days":
        cols = ["date", "steps", "rope_jumps", "km", "active_min", "kcal_total", "kcal_active", "kcal_min", "kcal_max",
                "bmr", "rhr", "rhr_source", "hrv_night_ms", "sleep_min", "deep_min", "rem_min", "awake_min",
                "zepp_sleep_score", "spo2", "breathing", "temperature", "banister_load", "who_zone_min", "workouts",
                "weight_kg", "vo2max_est"]
    else:
        cols = ["date", "start", "end", "type", "name", "confirmed", "source", "duration_min", "steps", "jumps",
                "hr_avg", "hr_max", "kcal_active", "kcal_min", "kcal_max", "kcal_method", "banister_load", "load_kg"]
    return csv_text(cols, rows, fmt, lang)


# ---------------------------------------------------------------- HTTP

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
    "/i18n/it.json": ("i18n/it.json", "application/json; charset=utf-8"),
    "/i18n/en.json": ("i18n/en.json", "application/json; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
}


def parse_iso(v):
    """Date YYYY-MM-DD required (for saves): no fallback to today."""
    if not isinstance(v, str) or not DATE_RE.match(v):
        raise UserError("e.date_invalid")
    return date.fromisoformat(v)


def settings_load():
    """Interface settings (data folder, included in export and import).
    lang: "it", "en" or None = system language."""
    s = state_load("settings.json")
    return {"lang": s.get("lang") if s.get("lang") in LANGS else None}


def save_settings(body):
    lang = body.get("lang")
    if lang is not None and lang not in LANGS:
        raise UserError("e.lang_invalid")
    with _labels_lock:
        s = state_load("settings.json")
        s["lang"] = lang
        state_save("settings.json", s)
    return {"ok": True, **settings_load()}


def parse_day(q, key):
    v = q.get(key, [None])[0]
    if v and DATE_RE.match(v):
        try:
            return date.fromisoformat(v)
        except ValueError:
            pass
    return now().date()


class Handler(BaseHTTPRequestHandler):
    server_version = "helio"
    sys_version = ""
    require_auth = False
    timeout = 60  # a stalled connection (phone switching network) does not hold a thread forever

    def _authorized(self):
        self._new_cookie = False
        if not self.require_auth or LAN_OPEN:
            return True
        if not AUTH_PASS:
            return False
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and hmac.compare_digest(v.encode(), session_token().encode()):
                return True
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(h[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        ok_u = hmac.compare_digest(user.encode(), AUTH_USER.encode())
        ok_p = hmac.compare_digest(pw.encode(), AUTH_PASS.encode())
        if ok_u and ok_p:
            self._new_cookie = True  # remember this device
        return ok_u and ok_p

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype, cache="no-store", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if getattr(self, "_new_cookie", False):
            self.send_header("Set-Cookie", f"{COOKIE}={session_token()}; Max-Age={REMEMBER_DAYS * 86400}; "  # elapsed
                                           "Path=/; Secure; HttpOnly; SameSite=Strict")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _deny(self):
        time.sleep(1)  # slows down password guessing
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Ritmo", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, code, obj):
        return self._send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        if not self._authorized():
            return self._deny()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/summary":
                return self._json(200, summary(parse_day(q, "date")))
            if u.path == "/api/settings":
                return self._json(200, settings_load())
            if u.path == "/api/trends":
                return self._json(200, trends(parse_day(q, "end")))
            if u.path == "/api/vitals":
                return self._json(200, vitals(parse_day(q, "end")))
            if u.path == "/api/week":
                which = q.get("which", ["auto"])[0]
                return self._json(200, week_summary(parse_day(q, "date"), which if which in ("auto", "current", "previous") else "auto"))
            if u.path == "/api/strength":
                fd = q.get("day", [""])[0]
                return self._json(200, strength_day(parse_day(q, "date"), int(fd) if fd in ("1", "2", "3") else None))
            if u.path == "/api/plan":
                return self._json(200, plan_payload())
            if u.path == "/api/weight":
                return self._json(200, weight_payload())
            if u.path == "/api/profile":
                return self._json(200, profile_payload())
            if u.path == "/api/foods":
                return self._json(200, foods_payload())
            if u.path == "/api/nutrition":
                return self._json(200, nutrition_day(parse_day(q, "date")))
            if u.path == "/api/catalog/search":
                lim = q.get("limit", ["30"])[0]
                return self._json(200, catalog_search(q.get("q", [""])[0], q.get("lang", [None])[0],
                                                      int(lim) if lim.isdigit() and 0 < int(lim) <= 50 else 30))
            if u.path == "/api/catalog/barcode":
                return self._json(200, catalog_barcode(q.get("code", [""])[0], q.get("lang", [None])[0]))
            if u.path == "/api/catalog/info":
                return self._json(200, catalog_info())
            if u.path == "/api/catalog/bench":
                return self._json(200, catalog_bench(q.get("lang", [None])[0]))
            if u.path == "/api/export.csv":
                kind = code(q.get("kind", ["days"])[0])
                fmt = "std" if q.get("fmt", ["eu"])[0] == "std" else "eu"  # anything else, also the old "it", is "eu"
                lang = q.get("lang", ["en"])[0] if q.get("lang", ["en"])[0] in LANGS else "en"
                d1 = parse_day(q, "to")
                d0 = parse_day(q, "from") if q.get("from") else d1 - timedelta(days=29)
                try:
                    text = export_csv(kind, d0, d1, fmt, lang)
                except UserError as e:
                    return self._json(400, {"error": e.msg})
                name = f"helio-{kind if kind in EXPORT_KINDS else 'dati'}-{min(d0, d1)}_{max(d0, d1)}.csv"
                return self._send(200, text, "text/csv; charset=utf-8", extra={"Content-Disposition": f'attachment; filename="{name}"'})
        except (BrokenPipeError, ConnectionResetError):
            return  # the phone closed the page before the answer: nothing to do
        except UserError as e:
            return self._json(400, {"error": e.msg})
        except sqlite3.Error as e:
            print(f"Database not readable on {u.path}: {e!r}", flush=True)
            return self._json(503, {"error": M("err.db_unreadable")})
        except Exception as e:  # never leave the phone without an answer
            print(f"Error on {u.path}: {e!r}", flush=True)
            return self._json(500, {"error": M("err.internal")})
        if u.path in STATIC_FILES:
            name, ctype = STATIC_FILES[u.path]
            try:
                return self._send(200, (STATIC / name).read_bytes(), ctype, "no-cache")
            except OSError:
                pass
        self._send(404, "Not found", "text/plain; charset=utf-8")

    POST_LIMITS = {"/api/label": 4096, "/api/strength": 32768, "/api/plan": 32768, "/api/weight": 1024,
                   "/api/food": 8192, "/api/meal": 8192, "/api/diary": 4096, "/api/goal": 512,
                   "/api/profile": 2048, "/api/settings": 256, "/api/catalog": 1024}

    def do_POST(self):
        if not self._authorized():
            return self._deny()
        u = urlparse(self.path)
        if u.path not in self.POST_LIMITS:
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        # custom header + JSON: an external site cannot send this request from a browser
        if self.headers.get("X-Helio") != "1" or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json(403, {"error": M("err.forbidden")})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if not 0 < n <= self.POST_LIMITS[u.path]:
            return self._json(400, {"error": M("err.invalid_request")})
        try:
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise UserError("err.invalid_request")
            if u.path == "/api/label":
                res = save_label(int(body["start"]), int(body["end"]), body.get("type"),
                                 int(body["day"]) if body.get("day") else None,
                                 float(body["load_kg"]) if body.get("load_kg") not in (None, "") else None,
                                 bool(body.get("manual")), body.get("work_level"))
            elif u.path == "/api/strength":
                res = save_strength(parse_iso(body.get("date")), body.get("entries"), body.get("load_kg"))
            elif u.path == "/api/plan":
                res = save_plan(body)
            elif u.path == "/api/food":
                res = save_food(body)
            elif u.path == "/api/meal":
                res = save_meal(body)
            elif u.path == "/api/diary":
                res = diary_op(body)
            elif u.path == "/api/goal":
                res = save_goal(body)
            elif u.path == "/api/profile":
                res = save_profile(body)
            elif u.path == "/api/settings":
                res = save_settings(body)
            elif u.path == "/api/catalog":
                res = catalog_op(body)
            else:
                res = save_weight(parse_iso(body.get("date")), body.get("kg"))
        except UserError as e:
            return self._json(400, {"error": e.msg})
        except (ValueError, KeyError, TypeError) as e:
            print(f"Invalid request on {u.path}: {e!r}", flush=True)
            return self._json(400, {"error": M("err.invalid_request")})
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, sqlite3.Error) as e:
            print(f"Save failed on {u.path}: {e!r}", flush=True)
            return self._json(500, {"error": M("err.save_failed")})
        except Exception as e:  # never leave the phone without an answer
            print(f"Save failed: {e!r}", flush=True)
            return self._json(500, {"error": M("err.internal_retry")})
        try:
            return self._json(200, res)
        except (BrokenPipeError, ConnectionResetError):
            return


class LanHandler(Handler):
    require_auth = True


class TLSServer(ThreadingHTTPServer):
    """HTTPS with the TLS handshake done in each connection's thread.

    With ctx.wrap_socket on the listening socket the handshake happens inside accept(), in the main
    thread: a single half-open connection (phone switching from Wi-Fi to mobile data, Wi-Fi
    dropping) blocks every following request, and the app hangs on "Saving…" or on loading.
    """
    daemon_threads = True
    handshake_timeout = 15

    def __init__(self, addr, handler, ctx):
        super().__init__(addr, handler)
        self.ctx = ctx

    def finish_request(self, request, client_address):
        request.settimeout(self.handshake_timeout)
        try:
            conn = self.ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            self.RequestHandlerClass(conn, client_address, self)
        finally:
            try:
                conn.close()
            except OSError:
                pass


def tls_context(cert, key):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


def start_lan():
    """Second entrance for the home network: HTTPS with the Tailscale certificate and a password.

    When the container restarts the network address may not be ready yet:
    in that case it retries every 5 seconds instead of giving up.
    """
    if not TLS_BIND:
        return
    if not AUTH_PASS and not LAN_OPEN:
        print("LAN access disabled: HELIO_PASSWORD is not set", flush=True)
        return

    def run():
        warned = False
        while True:
            try:
                srv = TLSServer((TLS_BIND, TLS_PORT), LanHandler, tls_context(TLS_CERT, TLS_KEY))
            except (OSError, ssl.SSLError) as e:
                if not warned:
                    print(f"LAN access not available yet ({e}), retrying every 5 seconds", flush=True)
                    warned = True
                time.sleep(5)
                continue
            print(f"Ritmo listening on https://{TLS_BIND}:{TLS_PORT} (home network, "
                  + ("no password)" if LAN_OPEN else f"password, device remembered for {REMEMBER_DAYS} days)"),
                  flush=True)
            srv.serve_forever()

    threading.Thread(target=run, daemon=True).start()


def migrate_state_dir():
    """Converts the old codes in every state file at start (for example right after importing an old backup)."""
    try:
        names = [p.name for p in STATE_DIR.glob("*.json") if p.is_file()]
    except OSError:
        return
    with _labels_lock:
        for name in names:
            state_load(name)


migrate_state_dir()

if __name__ == "__main__":
    start_lan()
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    srv.daemon_threads = True
    print(f"Ritmo listening on http://{BIND}:{PORT} (database: {DB})", flush=True)
    srv.serve_forever()
