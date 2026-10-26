"""Tests of the bridge between the WebView and Helio (helio_android.py), on the computer.

    python3 -m unittest discover -s app/src/helio/python-tests

Every answer of the bridge must be identical to the real HTTP server's (same Handler),
without the bridge ever opening a socket.
"""
from contextlib import closing
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

HELIO_SRC = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HELIO_SRC / "python"), str(HELIO_SRC / "helio-app"), str(HELIO_SRC / "helio-app" / "tests")]

import helio_android  # noqa: E402
from test_helio import D0, DAY, build_db  # noqa: E402

NOW = str(D0 + 23 * 3600 + 59 * 60)
GET_PATHS = [
    "/api/summary?date=2026-03-10",
    "/api/summary",
    "/api/trends?end=2026-03-10",
    "/api/vitals?end=2026-03-10",
    "/api/week?date=2026-03-10&which=current",
    "/api/strength?date=2026-03-10",
    "/api/plan",
    "/api/weight",
    "/api/foods",
    "/api/nutrition?date=2026-03-10",
    "/api/export.csv?kind=giorni&from=2026-03-01&to=2026-03-10",
    "/api/nonesiste",
]
COMPARED_HEADERS = ("Content-Type", "Content-Security-Policy", "Cache-Control", "Content-Disposition",
                    "X-Content-Type-Options", "Referrer-Policy")
JSON = {"Content-Type": "application/json", "X-Helio": "1"}


def headers_of(result):
    return {k: v for k, v in json.loads(result[2])}


class BridgeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.db = cls.dir / "gb.db"
        build_db(cls.db)
        os.environ["HELIO_NOW"] = NOW
        os.environ["HELIO_STRIDE_M"] = "0.72"
        cls.h = helio_android.configure(str(cls.db), str(cls.dir / "state"), None, "Europe/Rome")

    @classmethod
    def tearDownClass(cls):
        for k in ("HELIO_NOW", "HELIO_STRIDE_M", "HELIO_DB_LIVE", "HELIO_STATE", "HELIO_PLAN", "HELIO_DEVICE_ID"):
            os.environ.pop(k, None)
        cls.tmp.cleanup()


class SameAsServer(BridgeBase):
    """The bridge and the real HTTP server give the same answer, byte for byte."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.h.Handler)  # only in the test, for the comparison
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def server(self, method, path, headers=None, body=None):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def assertSame(self, method, path, headers=None, body=b""):
        status, srv_headers, srv_body = self.server(method, path, headers, body if method == "POST" else None)
        result = helio_android.handle(method, path, headers or {}, body)
        self.assertEqual(result[0], status, path)
        self.assertEqual(result[3], srv_body, path)
        bridge_headers = headers_of(result)
        for k in COMPARED_HEADERS:
            self.assertEqual(bridge_headers.get(k), srv_headers.get(k), f"{path}: {k}")

    def test_get_endpoints(self):
        for path in GET_PATHS:
            with self.subTest(path=path):
                self.assertSame("GET", path)

    def test_post_rules(self):
        body = json.dumps({"date": "2026-03-10", "kg": 80.5}).encode()
        cases = [
            ("without X-Helio", "/api/weight", {"Content-Type": "application/json"}, body),
            ("X-Helio sbagliata", "/api/weight", {"Content-Type": "application/json", "X-Helio": "0"}, body),
            ("without JSON", "/api/weight", {"Content-Type": "text/plain", "X-Helio": "1"}, body),
            ("vuota", "/api/weight", JSON, b""),
            ("over the limit", "/api/weight", JSON, b"{" + b" " * 1100 + b"}"),
            ("non JSON", "/api/weight", JSON, b"xyz"),
            ("percorso sconosciuto", "/api/nonesiste", JSON, body),
            ("invalid date", "/api/weight", JSON, json.dumps({"date": "yesterday", "kg": 80}).encode()),
        ]
        for name, path, headers, data in cases:
            with self.subTest(name):
                self.assertSame("POST", path, headers, data)

    def test_post_saves_like_server(self):
        body = json.dumps({"date": "2026-03-09", "kg": 81.2}).encode()
        result = helio_android.handle("POST", "/api/weight", JSON, body)
        self.assertEqual(result[0], 200)
        self.assertEqual(json.loads(result[3]), {"ok": True})
        weights = json.loads((self.dir / "state" / "weights.json").read_text())
        self.assertIn("2026-03-09", json.dumps(weights))
        self.assertEqual(self.server("GET", "/api/weight")[2], helio_android.handle("GET", "/api/weight")[3])

    def test_headers_from_java_as_json(self):
        body = json.dumps({"date": "2026-03-08", "kg": 80}).encode()
        result = helio_android.handle("POST", "/api/weight", json.dumps(JSON), body)
        self.assertEqual(result[0], 200)


class PhoneTimeZone(BridgeBase):
    """The phone's time zone is read at every request: if it changes while Ritmo is open (a trip), the days start at
    the new midnight and nothing computed with the old zone is used again."""

    def tearDown(self):
        helio_android._zone_source = helio_android._phone_zone
        helio_android.set_time_zone("Europe/Rome")

    def summary(self):
        return json.loads(helio_android.handle("GET", "/api/summary?date=2026-03-10", {}, b"")[3])

    def test_outside_the_app_there_is_no_java(self):
        self.assertIsNone(helio_android._phone_zone())
        self.assertEqual(self.summary()["tz"], "Europe/Rome")

    def test_follows_the_phone(self):
        before = self.summary()
        self.assertEqual(before["tz"], "Europe/Rome")
        helio_android._zone_source = lambda: "America/New_York"
        after = self.summary()
        self.assertEqual(after["tz"], "America/New_York")
        self.assertEqual(after["day_start"], int(datetime(2026, 3, 10, tzinfo=ZoneInfo("America/New_York")).timestamp()))
        # on 10 March New York already has summer time (-4, since the 8th), Rome not yet (+1): 5 hours, not 6
        self.assertEqual(after["day_start"] - before["day_start"], 5 * 3600)
        self.assertEqual(os.environ["HELIO_TZ"], "America/New_York")

    def test_same_zone_keeps_what_was_computed(self):
        helio_android._zone_source = lambda: "Europe/Rome"
        self.summary()
        with mock.patch.object(self.h, "clear_caches") as cleared:
            self.summary()
        cleared.assert_not_called()


class Guards(BridgeBase):
    def test_static_files_never_from_python(self):
        for path in ("/", "/index.html", "/app.js", "/sw.js"):
            with self.subTest(path=path):
                self.assertEqual(helio_android.handle("GET", path)[0], 404)

    def test_other_methods_rejected(self):
        self.assertEqual(helio_android.handle("PUT", "/api/plan")[0], 400)
        self.assertEqual(helio_android.handle("DELETE", "/api/plan")[0], 400)

    def test_no_header_injection(self):
        self.assertEqual(helio_android.handle("GET", "/api/plan\r\nX-Helio: 1")[0], 400)
        self.assertEqual(helio_android.handle("GET", "/api/plan HTTP/1.0")[0], 400)
        # a header with a newline is dropped: without X-Helio the POST is refused
        body = json.dumps({"date": "2026-03-10", "kg": 80}).encode()
        bad = {"Content-Type": "application/json", "X-Helio": "1\r\nX-Altro: 1"}
        self.assertEqual(helio_android.handle("POST", "/api/weight", bad, body)[0], 403)

    def test_only_needed_headers_reach_handler(self):
        seen = {}
        original = self.h.Handler.do_GET

        def spy(handler):
            seen.update(handler.headers.items())
            return original(handler)

        with mock.patch.object(self.h.Handler, "do_GET", spy):
            helio_android.handle("GET", "/api/plan", {"Cookie": "a=b", "Authorization": "Basic eA==",
                                                      "X-Helio": "1", "User-Agent": "x"})
        self.assertEqual(seen.get("X-Helio"), "1")
        for name in ("Cookie", "Authorization", "User-Agent"):
            self.assertNotIn(name, seen)

    def test_huge_body_is_not_read(self):
        body = b"x" * (helio_android.MAX_BODY + 1)
        result = helio_android.handle("POST", "/api/plan", JSON, body)
        self.assertEqual(result[0], 400)
        self.assertEqual(json.loads(result[3]), {"error": {"$": "err.invalid_request"}})

    def test_no_socket_is_ever_opened(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("socket opened")), \
                mock.patch.object(socket, "create_connection", side_effect=AssertionError("connection")):
            for path in GET_PATHS:
                self.assertIn(helio_android.handle("GET", path)[0], (200, 404))
            body = json.dumps({"date": "2026-03-07", "kg": 80}).encode()
            self.assertEqual(helio_android.handle("POST", "/api/weight", JSON, body)[0], 200)


class Configure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        for k in ("HELIO_DB_LIVE", "HELIO_STATE", "HELIO_PLAN", "HELIO_DEVICE_ID", "HELIO_TZ", "HELIO_HEIGHT"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def make_db(self, rows, with_type=True):
        db = self.dir / "gb.db"
        with closing(sqlite3.connect(db)) as con:
            if with_type:
                # as in Gadgetbridge: IDENTIFIER is the address, TYPE_NAME the DeviceType name
                con.execute("CREATE TABLE DEVICE (_id INTEGER PRIMARY KEY, NAME TEXT, TYPE_NAME TEXT, IDENTIFIER TEXT)")
                con.executemany("INSERT INTO DEVICE VALUES (?, ?, ?, ?)",
                                [row + (f"AA:BB:CC:DD:EE:0{row[0]}",) for row in rows])
            else:
                con.execute("CREATE TABLE DEVICE (_id INTEGER PRIMARY KEY, NAME TEXT)")
                con.executemany("INSERT INTO DEVICE VALUES (?, ?)", rows)
            con.commit()
        return db

    def test_finds_helio_strap(self):
        db = self.make_db([(1, "Bip", "AMAZFITBIP"), (4, "Amazfit Helio Strap", "AMAZFITHELIOSTRAP")])
        self.assertEqual(helio_android.find_device_id(db), 4)
        self.assertEqual(helio_android.find_device(db), (4, "AA:BB:CC:DD:EE:04"))

    def test_type_name_is_the_gadgetbridge_enum(self):
        """DEVICE.TYPE_NAME is DeviceType.name(): AMAZFITHELIOSTRAP for the band, without underscores."""
        enum = (HELIO_SRC.parent / "main/java/nodomain/freeyourgadget/gadgetbridge/model/DeviceType.java").read_text()
        self.assertIn(f"    {helio_android.HELIO_TYPE}(", enum)

    def test_device_fallbacks(self):
        self.assertEqual(helio_android.find_device_id(self.make_db([(3, "X")], with_type=False)), 3)
        self.assertEqual(helio_android.find_device_id(self.dir / "manca.db"), 1)

    def test_server_settings_removed_and_live_db(self):
        db = self.make_db([(2, "Helio", "AMAZFITHELIOSTRAP")])
        os.environ.update({"HELIO_PASSWORD": "segreta", "HELIO_TLS_BIND": "0.0.0.0", "HELIO_BIND": "0.0.0.0"})
        h = helio_android.configure(str(db), str(self.dir / "state"), None, "Europe/Rome", {"HELIO_HEIGHT": "171"})
        for k in ("HELIO_PASSWORD", "HELIO_TLS_BIND", "HELIO_BIND"):
            self.assertNotIn(k, os.environ)
        self.assertTrue(h.DB_LIVE)
        self.assertEqual(h.DEVICE_ID, 2)
        self.assertEqual(h.AUTH_PASS, "")
        self.assertEqual(h.TLS_BIND, "")
        self.assertEqual(os.environ["HELIO_HEIGHT"], "171")
        self.assertEqual(h.PLAN_FILE, "")  # no default plan in the app
        self.assertEqual(h.load_plan(), {})


BAND_XML = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="AA:BB:CC:DD:EE:02/state">{state}</string>
    <long name="AA:BB:CC:DD:EE:02/updated" value="1791800000000" />
    <long name="AA:BB:CC:DD:EE:02/last_sync" value="1791799000000" />
    <string name="AA:BB:CC:DD:EE:02/busy">{busy}</string>
    <string name="11:22:33:44:55:66/state">INITIALIZED</string>
</map>
"""


class AndroidApi(unittest.TestCase):
    """/api/android/*: band status (read-only) and checks of the actions for Gadgetbridge."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "gb.db"
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE DEVICE (_id INTEGER PRIMARY KEY, NAME TEXT, TYPE_NAME TEXT, IDENTIFIER TEXT)")
            con.execute("INSERT INTO DEVICE VALUES (1, 'Altro', 'AMAZFITBIP', '11:22:33:44:55:66')")
            con.execute("INSERT INTO DEVICE VALUES (2, 'Helio', 'AMAZFITHELIOSTRAP', 'AA:BB:CC:DD:EE:02')")
            con.commit()
        self.prefs = self.dir / "helio_band_state.xml"
        helio_android.configure(str(self.db), str(self.dir / "state"), None, "Europe/Rome",
                                band_state_path=str(self.prefs))

    def tearDown(self):
        for k in ("HELIO_DB_LIVE", "HELIO_STATE", "HELIO_PLAN", "HELIO_DEVICE_ID", "HELIO_TZ"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def band(self, running="running", extra=None):
        headers = {"X-Helio-Gb-Process": running}
        headers.update(extra or {})
        result = helio_android.handle("GET", "/api/android/band", headers)
        self.assertEqual(result[0], 200)
        self.assertEqual(headers_of(result)["Cache-Control"], "no-store")
        return json.loads(result[3])

    def action(self, body, headers=None):
        result = helio_android.handle("POST", "/api/android/action", JSON if headers is None else headers,
                                      body if isinstance(body, bytes) else json.dumps(body).encode())
        return result[0], json.loads(result[3]), headers_of(result).get("X-Helio-Action")

    def test_connected_band(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy=""))
        b = self.band()
        self.assertEqual((b["connected"], b["status"], b["state"]), (True, "connected", "INITIALIZED"))
        self.assertEqual(b["last_sync"], 1791799000)
        self.assertEqual(b["updated"], 1791800000)
        self.assertIsNone(b["busy"])
        self.assertTrue(b["known"])

    def test_states(self):
        for state, status in (("CONNECTING", "connecting"), ("INITIALIZING", "connecting"),
                              ("NOT_CONNECTED", "disconnected"), ("WAITING_FOR_RECONNECT", "disconnected")):
            self.prefs.write_text(BAND_XML.format(state=state, busy="Fetching activity data"))
            b = self.band()
            self.assertEqual((b["status"], b["connected"]), (status, False), state)
            self.assertEqual(b["busy"], "Fetching activity data")

    def test_gadgetbridge_not_running(self):
        # the saved state can be old: if Gadgetbridge is not running, the band is not connected
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy="x"))
        for running in ("stopped", None, "1", "true"):
            b = self.band(running)
            self.assertEqual((b["connected"], b["status"], b["busy"]), (False, "gb_stopped", None))
        self.assertEqual(b["last_sync"], 1791799000)  # the last sync stays valid

    def test_no_state_yet_or_broken_file(self):
        b = self.band()
        self.assertEqual((b["known"], b["status"], b["last_sync"]), (False, "disconnected", None))
        self.prefs.write_text("<map><string name=")
        self.assertFalse(self.band()["known"])

    def test_band_is_read_only_get(self):
        before = self.prefs.read_bytes() if self.prefs.exists() else None
        result = helio_android.handle("POST", "/api/android/band", JSON, b"{}")
        self.assertEqual(result[0], 405)
        self.assertEqual(self.prefs.read_bytes() if self.prefs.exists() else None, before)

    def test_valid_actions_are_approved(self):
        for a in helio_android.ANDROID_ACTIONS:
            status, body, approved = self.action({"action": a})
            self.assertEqual((status, body, approved), (200, {"ok": True, "action": a}, a))

    def test_actions_match_java_and_the_band_menu(self):
        # the bridge approves exactly what Java runs (HelioGbActions.ALL), and each band menu button is one of them
        import re
        java = (HELIO_SRC / "java/nodomain/freeyourgadget/gadgetbridge/helio/HelioGbActions.java").read_text()
        consts = dict(re.findall(r'String (\w+) = "(\w+)";', java))
        names = re.search(r"ALL = .*?asList\(([^)]*)\)", java, re.S).group(1)
        self.assertEqual(sorted(consts[n.strip()] for n in names.split(",")), sorted(helio_android.ANDROID_ACTIONS))
        html = (HELIO_SRC / "helio-app/static/index.html").read_text()
        buttons = re.findall(r'data-band-action="(\w+)"', html)
        self.assertIn("authkey", buttons)
        self.assertTrue(set(buttons) <= set(helio_android.ANDROID_ACTIONS), buttons)

    def test_actions_need_the_same_checks_as_handler(self):
        ok = {"action": "sync"}
        cases = [
            ("without X-Helio", ok, {"Content-Type": "application/json"}, 403),
            ("X-Helio sbagliata", ok, {"Content-Type": "application/json", "X-Helio": "0"}, 403),
            ("without JSON", ok, {"Content-Type": "text/plain", "X-Helio": "1"}, 403),
            ("vuota", b"", JSON, 400),
            ("too large", {"action": "sync", "x": "a" * 300}, JSON, 400),
            ("non JSON", b"sync", JSON, 400),
            ("non un oggetto", ["sync"], JSON, 400),
            ("azione sconosciuta", {"action": "reboot"}, JSON, 400),
            ("azione mancante", {}, JSON, 400),
        ]
        for name, body, headers, code in cases:
            with self.subTest(name):
                status, _, approved = self.action(body, headers)
                self.assertEqual(status, code)
                self.assertIsNone(approved)

    def test_actions_only_by_post(self):
        result = helio_android.handle("GET", "/api/android/action?action=sync", JSON)
        self.assertEqual(result[0], 405)
        self.assertNotIn("X-Helio-Action", headers_of(result))

    def test_unknown_android_path(self):
        self.assertEqual(helio_android.handle("GET", "/api/android/nope")[0], 404)
        self.assertEqual(helio_android.handle("POST", "/api/android/nope", JSON, b"{}")[0], 404)

    # pull down to sync: "pull" becomes "sync" only when needed
    LAST = 1791799000  # last_sync of BAND_XML, in seconds

    def pull(self, wall, mono, running="running", headers=None):
        h = dict(JSON if headers is None else headers)
        h["X-Helio-Gb-Process"] = running
        with mock.patch.object(helio_android, "_wall", lambda: wall), \
                mock.patch.object(helio_android, "_monotonic", lambda: mono):
            return self.action({"action": "pull"}, h)

    def test_pull_starts_a_sync_when_idle(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy=""))
        status, body, approved = self.pull(self.LAST + 3600, 1000.0)
        self.assertEqual((status, body["sync"], approved), (200, True, "sync"))

    def test_pull_again_within_30_seconds_only_refreshes(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy=""))
        self.assertEqual(self.pull(self.LAST + 3600, 1000.0)[2], "sync")
        # Gadgetbridge has not set "busy" yet: Helio's request counts
        for later in (0.5, 10, 29.9):
            status, body, approved = self.pull(self.LAST + 3600 + later, 1000.0 + later)
            self.assertEqual((status, body["sync"], body["reason"], approved), (200, False, "recent", None), later)
        self.assertEqual(self.pull(self.LAST + 3631, 1031.0)[2], "sync")

    def test_pull_while_syncing_only_refreshes(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy="Fetching activity data"))
        status, body, approved = self.pull(self.LAST + 3600, 1000.0)
        self.assertEqual((status, body["sync"], body["reason"], approved), (200, False, "busy", None))

    def test_pull_after_a_recent_sync_only_refreshes(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy=""))
        for after, expected in ((0, None), (20, None), (29, None), (30, "sync")):
            helio_android._last_sync_request = None
            self.assertEqual(self.pull(self.LAST + after, 1000.0)[2], expected, after)

    def test_pull_with_clock_behind_last_sync(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy=""))
        self.assertEqual(self.pull(self.LAST - 3600, 1000.0)[2], "sync")

    def test_pull_ignores_stale_busy_when_gadgetbridge_is_not_running(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy="Fetching activity data"))
        self.assertEqual(self.pull(self.LAST + 3600, 1000.0, running="stopped")[2], "sync")

    def test_pull_without_band_state(self):
        self.assertEqual(self.pull(self.LAST, 1000.0)[2], "sync")

    def test_menu_sync_always_runs_and_counts_for_pull(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy=""))
        with mock.patch.object(helio_android, "_monotonic", lambda: 1000.0):
            self.assertEqual(self.action({"action": "sync"})[2], "sync")
            self.assertEqual(self.action({"action": "sync"})[2], "sync")  # the button stays as it is
        self.assertIsNone(self.pull(self.LAST + 3600, 1010.0)[2])

    def test_pull_needs_the_same_checks_as_handler(self):
        self.prefs.write_text(BAND_XML.format(state="INITIALIZED", busy=""))
        for headers, code in (({"Content-Type": "application/json"}, 403),
                              ({"Content-Type": "text/plain", "X-Helio": "1"}, 403)):
            status, _, approved = self.pull(self.LAST + 3600, 1000.0, headers=headers)
            self.assertEqual((status, approved), (code, None))
        # a refused request does not count as a requested sync
        self.assertEqual(self.pull(self.LAST + 3600, 1000.0)[2], "sync")

    def test_pull_is_not_a_gadgetbridge_action(self):
        # Java runs only the actions in HelioGbActions: "pull" gets there only as "sync"
        self.assertNotIn(helio_android.PULL_ACTION, helio_android.ANDROID_ACTIONS)

    def test_handler_never_approves_actions(self):
        # Helio's API (Handler) never produces the header that makes an action run
        body = json.dumps({"date": "2026-03-10", "kg": 80}).encode()
        for method, path, data in (("GET", "/api/plan", b""), ("POST", "/api/weight", body)):
            self.assertNotIn("X-Helio-Action", headers_of(helio_android.handle(method, path, JSON, data)))


class Transfer(unittest.TestCase):
    """Export Helio data -> import: same files and same profile, in the server's format."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "helio-state"
        self.state.mkdir()
        (self.state / "weights.json").write_text('{"2026-03-10": 80.5}')
        (self.state / "labels.json").write_text('{"x": {"type": "run"}}')
        (self.state / "diary-2026-03.json").write_text('{"2026-03-10": []}')
        self.profile = self.dir / "helio-config.json"
        self.profile.write_text(json.dumps({"HELIO_HEIGHT": "171", "HELIO_WEIGHT": "74", "HELIO_PASSWORD": "x"}))

    def tearDown(self):
        for k in ("HELIO_DB_LIVE", "HELIO_STATE", "HELIO_PLAN", "HELIO_DEVICE_ID", "HELIO_TZ", "HELIO_HEIGHT",
                  "HELIO_WEIGHT"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def files(self, d):
        return {p.name: p.read_bytes() for p in sorted(Path(d).iterdir())}

    def test_round_trip(self):
        data = helio_android.export_state(str(self.state), str(self.profile))
        other_state = self.dir / "other" / "helio-state"
        other_profile = self.dir / "other" / "helio-config.json"
        summary = json.loads(helio_android.import_state(data, str(other_state), str(other_profile)))
        self.assertEqual(summary, {"files": 3, "profile": ["HELIO_HEIGHT", "HELIO_WEIGHT"], "backup": False})
        self.assertEqual(self.files(other_state), self.files(self.state))
        self.assertEqual(json.loads(other_profile.read_text()), {"HELIO_HEIGHT": "171", "HELIO_WEIGHT": "74"})
        # again: same archive, same files
        again = helio_android.export_state(str(other_state), str(other_profile))
        import helio_transfer
        self.assertEqual(helio_transfer.read_archive(again), helio_transfer.read_archive(data))

    def test_import_from_java_bytes(self):
        data = bytearray(helio_android.export_state(str(self.state), str(self.profile)))
        target = self.dir / "t"
        helio_android.import_state(data, str(target), str(self.dir / "p.json"))
        self.assertEqual(self.files(target), self.files(self.state))

    def test_bad_archive_changes_nothing(self):
        import helio_transfer
        before = self.files(self.state)
        with self.assertRaises(helio_android.ImportFailed) as cm:
            helio_android.import_state(b"non un archivio", str(self.state), str(self.profile), "it")
        self.assertIn("archivio non leggibile", str(cm.exception))  # in the language of the native dialogs  # lang: it
        with self.assertRaises(helio_android.ImportFailed) as cm:
            helio_android.import_state(b"not an archive", str(self.state), str(self.profile), "en")
        self.assertIn("archive not readable", str(cm.exception))
        self.assertTrue(issubclass(helio_android.ImportFailed, ValueError))
        self.assertTrue(helio_transfer.TransferError)
        self.assertEqual(self.files(self.state), before)

    def test_old_backup_is_converted_when_the_app_starts(self):
        """An archive exported by versionCode 259 (Italian codes): after the import the app restarts and the
        bridge's configure() converts every state file to the English codes."""
        (self.state / "labels.json").write_text('{"x": {"type": "forza", "load_kg": 15}}')  # legacy
        (self.state / "nutrition.json").write_text('{"goal": "mantenere"}')                 # legacy
        data = helio_android.export_state(str(self.state), str(self.profile))
        target = self.dir / "phone" / "helio-state"
        helio_android.import_state(data, str(target), str(self.dir / "phone" / "helio-config.json"))
        db = self.dir / "gb.db"
        build_db(db)
        h = helio_android.configure(str(db), str(target), None, "Europe/Rome")
        self.assertEqual(json.loads((target / "labels.json").read_text())["x"]["type"], "strength")
        self.assertEqual(json.loads((target / "nutrition.json").read_text())["goal"], "maintain")
        self.assertEqual(h.nutrition_settings()["goal"], "maintain")

    def test_profile_file_applied_at_start(self):
        db = self.dir / "gb.db"
        build_db(db)
        os.environ["HELIO_HEIGHT"] = "150"  # a value left over from before must not count
        h = helio_android.configure(str(db), str(self.state), None, "Europe/Rome", profile_path=str(self.profile))
        self.assertEqual(os.environ["HELIO_HEIGHT"], "171")
        self.assertNotIn("HELIO_PASSWORD", os.environ)
        self.assertEqual(h.STATE_DIR, self.state)


if __name__ == "__main__":
    unittest.main()


class FoodCatalog(unittest.TestCase):
    """The food catalog on the phone: its folder next to the data, the package shipped in the APK, import from
    a file chosen by the user (reasons in the language of the native dialogs)."""

    def setUp(self):
        from test_catalog import build, sample_generic, sample_off
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "helio-state"
        self.state.mkdir()
        (self.state / "foods.json").write_text('{"foods": {}, "meals": {}}')
        self.cat = self.dir / "helio-catalog"
        self.generic = build(self.dir, sample_generic())
        self.off = build(self.dir, sample_off(), kind="off")
        db = self.dir / "gb.db"
        build_db(db)
        self.h = helio_android.configure(str(db), str(self.state), None, "Europe/Rome", catalog_dir=str(self.cat),
                                         catalog_bundled=str(self.generic))

    def tearDown(self):
        for k in ("HELIO_DB_LIVE", "HELIO_STATE", "HELIO_PLAN", "HELIO_DEVICE_ID", "HELIO_TZ", "HELIO_CATALOG",
                  "HELIO_CATALOG_BUNDLED"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def test_configure(self):
        self.assertEqual(self.h.CATALOG_DIR, self.cat)
        self.assertEqual(self.h.CATALOG_BUNDLED, str(self.generic))
        res = helio_android.handle("GET", "/api/catalog/search?q=mela&lang=it", {})
        self.assertEqual(json.loads(res[3])["community"][0]["name"], "Mela")
        self.assertEqual(sorted(p.name for p in self.cat.iterdir()), ["generic.json", "generic.sqlite"])

    def test_import_file(self):
        before = (self.state / "foods.json").read_bytes()
        summary = json.loads(helio_android.catalog_import(str(self.off), "it"))
        self.assertEqual((summary["kind"], summary["rows"]), ("off", 5))
        bad = self.dir / "bad.tar"
        bad.write_bytes(b"not a package")
        with self.assertRaises(helio_android.ImportFailed) as cm:
            helio_android.catalog_import(str(bad), "it")
        self.assertIn("pacchetto valido", str(cm.exception))  # lang: it
        with self.assertRaises(helio_android.ImportFailed) as cm:
            helio_android.catalog_import(str(self.generic), "en")
        self.assertIn("cannot be installed here", str(cm.exception))
        self.assertEqual((self.state / "foods.json").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.cat.iterdir()), ["off.json", "off.sqlite"])
