"""Bridge between the Helio Activity (WebView) and helio_app, without network.

/api/* requests come from Java as (method, path, headers, body) and go through the
same Handler class as the server, attached to a fake in-memory connection: no open
port, same checks and same answers (X-Helio, Content-Type, size limits, CSP).

/api/android/* exists only in the app and is handled here:
    GET  /api/android/band     band status written by Gadgetbridge (read-only)
    POST /api/android/action   checks an action for Gadgetbridge; Java runs it only if
                               this answer approves it (X-Helio-Action header)

The "pull" action (pull down to sync) becomes "sync" only if no sync is already
running and the last one, or the last one requested by Helio, is at least 30 s old:
otherwise the page only refreshes its data, without making the band work.
"""
from contextlib import closing
import importlib
import io
import json
import os
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET

import helio_transfer

# page headers that reach Handler: the others are not needed by the API
FORWARDED_HEADERS = ("content-type", "x-helio")
# hard limit before Handler, which then applies its own POST_LIMITS (all smaller)
MAX_BODY = 64 * 1024
HELIO_TYPE = "AMAZFITHELIOSTRAP"  # DeviceType.AMAZFITHELIOSTRAP.name() in DEVICE.TYPE_NAME
HOST = "appassets.androidplatform.net"
# server variables (network, password, TLS) that must not exist in the app
SERVER_ONLY_ENV = ("HELIO_BIND", "HELIO_PORT", "HELIO_TLS_BIND", "HELIO_TLS_PORT", "HELIO_TLS_CERT",
                   "HELIO_TLS_KEY", "HELIO_USER", "HELIO_PASSWORD", "HELIO_LAN_OPEN", "HELIO_REMEMBER_DAYS")

# actions the page can ask of Gadgetbridge (HelioGbActionActivity runs them)
ANDROID_ACTIONS = ("sync", "settings", "charts", "data", "gadgetbridge", "catalog_import", "scan", "authkey")
# pull down: a sync only when needed
PULL_ACTION = "pull"
SYNC_COOLDOWN = 30  # seconds
MAX_ACTION_BODY = 256
# GBDevice.State states
CONNECTING_STATES = ("CONNECTING", "CONNECTED", "INITIALIZING", "AUTHENTICATING")
CONNECTED_STATE = "INITIALIZED"
# header set by Java (HelioWebRouter), never by the page: is Gadgetbridge running?
GB_PROCESS_HEADER = "x-helio-gb-process"
ACTION_HEADER = "X-Helio-Action"

_app = None
_zone = None  # time zone helio_app works with (IANA name)
_band_address = None
_band_state_path = None
# last sync requested by Helio (monotonic clock): Gadgetbridge sets "busy" only
# when the transfer starts, so two pulls close together must not start two of them
_last_sync_request = None
_wall = time.time
_monotonic = time.monotonic


def find_device(db):
    """(ID, address) of the Helio Strap in Gadgetbridge's DEVICE table.

    Without a Helio Strap: the first device; without devices or database: (1, None).
    """
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)) as con:
            cols = {r[1] for r in con.execute('PRAGMA table_info("DEVICE")')}
            address = '"IDENTIFIER"' if "IDENTIFIER" in cols else "NULL"
            if "TYPE_NAME" in cols:
                row = con.execute(f'SELECT _id, {address} FROM "DEVICE" WHERE TYPE_NAME=? ORDER BY _id LIMIT 1',
                                  (HELIO_TYPE,)).fetchone()
                if row:
                    return row[0], row[1]
            row = con.execute(f'SELECT _id, {address} FROM "DEVICE" ORDER BY _id LIMIT 1').fetchone()
            return (row[0], row[1]) if row else (1, None)
    except sqlite3.Error:
        return 1, None


def find_device_id(db):
    """ID of the Helio Strap in Gadgetbridge's DEVICE table (1 if not found)."""
    return find_device(db)[0]


def configure(db, state_dir, plan, tz, profile=None, profile_path=None, band_state_path=None, catalog_dir=None,
              catalog_bundled=None):
    """Prepares the environment and loads helio_app. Call it once, before handle().

    The profile (measurements and goals) comes as a dictionary or from the JSON file profile_path.
    band_state_path: the preferences (XML) where Gadgetbridge writes the band status.
    catalog_dir: folder of the food catalog (next to state_dir, never inside); catalog_bundled: the catalog
    package shipped in the APK, copied out of the assets by the app.
    """
    global _app, _zone, _band_address, _band_state_path, _last_sync_request
    if profile is None and profile_path:
        profile = helio_transfer.load_profile(profile_path)
    for k in helio_transfer.PROFILE_KEYS:
        os.environ.pop(k, None)
    for k in SERVER_ONLY_ENV + ("HELIO_CATALOG", "HELIO_CATALOG_BUNDLED"):
        os.environ.pop(k, None)
    if catalog_dir:
        os.environ["HELIO_CATALOG"] = catalog_dir
    if catalog_bundled:
        os.environ["HELIO_CATALOG_BUNDLED"] = catalog_bundled
    os.environ.update(helio_transfer.clean_profile(profile or {}))
    os.environ.update({
        "HELIO_DB": db,
        "HELIO_DB_LIVE": "1",  # Gadgetbridge writes while Helio reads
        "HELIO_STATE": state_dir,
        "HELIO_PLAN": plan or "",  # no default plan: the user's plan lives in the data folder
        "HELIO_TZ": tz,
    })
    device_id, _band_address = find_device(db)
    os.environ["HELIO_DEVICE_ID"] = str(device_id)
    _band_state_path = band_state_path
    _last_sync_request = None
    if "helio_app" in sys.modules:
        _app = importlib.reload(sys.modules["helio_app"])
    else:
        import helio_app
        _app = helio_app
    _zone = tz
    return _app


def _phone_zone():
    """The phone's time zone now: Java's default, which Android updates in running apps when the system's changes
    (a trip, the automatic time zone). None outside the app."""
    try:
        from java import jclass  # Chaquopy
    except ImportError:
        return None
    try:
        return str(jclass("java.util.TimeZone").getDefault().getID())
    except Exception:
        return None


_zone_source = _phone_zone  # the tests replace it


def set_time_zone(tz):
    """Follows the phone into another time zone (a name, never an offset: zoneinfo gives each date its own, daylight
    saving included). Everything computed with the old zone is forgotten: days start at another midnight."""
    global _zone
    os.environ["HELIO_TZ"] = tz
    _app.TZ = _app.system_tz()
    _app.clear_caches()
    _zone = tz


def _follow_phone_zone():
    zone = _zone_source()
    if zone and zone != _zone:
        set_time_zone(zone)


class _Connection:
    """Fake socket: Handler reads the request from one buffer and writes the answer into another."""

    def __init__(self, raw):
        self._in = io.BytesIO(raw)
        self.sent = bytearray()

    def makefile(self, mode, bufsize=-1):
        if "r" not in mode:
            raise OSError("read only")
        return self._in

    def sendall(self, data):
        self.sent += data

    def settimeout(self, timeout):
        pass

    def setsockopt(self, *args):
        pass


class _Server:
    server_name = HOST
    server_port = 443


def _printable(value):
    return isinstance(value, str) and value != "" and all(0x20 <= ord(c) < 0x7f for c in value)


def _error(status, reason, message):
    body = json.dumps({"error": message}).encode()
    return [status, reason, json.dumps([["Content-Type", "application/json"]]), body]


def handle(method, path, headers=None, body=b""):
    """Runs an /api/* request with Handler. Returns [status, reason, JSON headers, body].

    headers is a dictionary or its JSON form (from Java).
    """
    if _app is None:
        raise RuntimeError("helio_android.configure() was not called")
    _follow_phone_zone()
    if isinstance(headers, str):
        headers = json.loads(headers)
    body = bytes(body or b"")
    if method not in ("GET", "POST") or not _printable(path) or " " in path:
        return _error(400, "Bad Request", {"$": "err.invalid_request"})
    if not path.startswith("/api/"):
        # static files come from the app assets, never from Python
        return _error(404, "Not Found", {"$": "err.not_found"})
    if path.startswith("/api/android/"):
        return _android_api(method, path, {str(k).lower(): v for k, v in (headers or {}).items()}, body)

    lines = [f"{method} {path} HTTP/1.1", f"Host: {HOST}", "Connection: close"]
    for name, value in (headers or {}).items():
        if str(name).lower() in FORWARDED_HEADERS and _printable(value):
            lines.append(f"{name}: {value}")
    if method == "POST":
        lines.append(f"Content-Length: {len(body)}")
        if len(body) > MAX_BODY:
            body = b""  # Handler sees the declared length and refuses without reading
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + (body if method == "POST" else b"")

    conn = _Connection(raw)
    _app.Handler(conn, ("127.0.0.1", 0), _Server())
    return _parse(bytes(conn.sent))


def _parse(data):
    head, _, payload = data.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    status = int(parts[1])
    reason = parts[2] if len(parts) > 2 else ""
    headers = []
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers.append([name.strip(), value.strip()])
    return [status, reason, json.dumps(headers), payload]


def export_state(state_dir, profile_path):
    """The .tgz archive with Helio's data and the profile (same format as the server)."""
    return helio_transfer.export_bytes(state_dir, helio_transfer.load_profile(profile_path))


class ImportFailed(ValueError):
    """Archive rejected, with the reason already translated for the native dialog."""


def import_state(data, state_dir, profile_path, lang="en"):
    """Replaces Helio's data with the archive's; the profile goes to the profile_path file.

    Returns a JSON summary. An invalid archive raises ImportFailed (reason in the language lang, the
    system one like the native dialogs) and changes nothing.
    """
    try:
        profile, n, backup = helio_transfer.import_bytes(bytes(data), state_dir)
    except helio_transfer.TransferError as e:
        raise ImportFailed(e.text(lang if lang in ("it", "en") else "en")) from None
    helio_transfer.save_profile(profile_path, profile)
    return json.dumps({"files": n, "profile": sorted(profile), "backup": backup is not None})


def catalog_import(path, lang="en"):
    """Installs a food catalog package chosen by the user (copied by the app into a temporary file).

    Returns a JSON summary. An invalid package raises ImportFailed (reason in the language lang) and leaves
    the installed catalog and the user's data as they are."""
    try:
        res = _app.catalog_install(path)
    except _app.UserError as e:
        lang = lang if lang in ("it", "en") else "en"
        raise ImportFailed(_app.tr(lang, e.msg["$"], **{k: v for k, v in e.msg.items() if k != "$"})) from None
    return json.dumps(res)


def _json_response(status, reason, obj, extra=None):
    headers = [["Content-Type", "application/json"], ["Cache-Control", "no-store"],
               ["X-Content-Type-Options", "nosniff"]] + (extra or [])
    return [status, reason, json.dumps(headers), json.dumps(obj).encode()]


def read_band_state(path, address):
    """The state saved by Gadgetbridge for a device, from its XML preferences (read-only)."""
    if not path or not address:
        return {}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    prefix = address + "/"
    out = {}
    for el in root:
        name = el.get("name", "")
        if not name.startswith(prefix):
            continue
        key = name[len(prefix):]
        if el.tag == "string":
            out[key] = el.text or ""
        elif el.tag in ("long", "int"):
            try:
                out[key] = int(el.get("value"))
            except (TypeError, ValueError):
                pass
        elif el.tag == "boolean":
            out[key] = el.get("value") == "true"
    return out


def band_status(gb_running):
    """Band connection and last sync, as Gadgetbridge sees them."""
    saved = read_band_state(_band_state_path, _band_address)
    state = saved.get("state") if gb_running else None
    # codes: the page translates them (band.status.<code>)
    if not gb_running:
        status = "gb_stopped"
    elif state == CONNECTED_STATE:
        status = "connected"
    elif state in CONNECTING_STATES:
        status = "connecting"
    else:
        status = "disconnected"
    last_sync = saved.get("last_sync")
    return {
        "known": bool(saved),
        "state": state,
        "connected": state == CONNECTED_STATE,
        "status": status,
        "busy": (saved.get("busy") or None) if gb_running else None,
        "last_sync": last_sync // 1000 if isinstance(last_sync, int) and last_sync > 0 else None,
        "updated": saved["updated"] // 1000 if isinstance(saved.get("updated"), int) else None,
    }


def _sync_requested():
    global _last_sync_request
    _last_sync_request = _monotonic()


def pull_decision(gb_running):
    """Pull down: "sync" (start), "busy" (already running) or "recent" (finished or requested shortly ago)."""
    band = band_status(gb_running)
    if band["busy"]:
        return "busy"
    # (a clock that went backwards does not block syncs)
    if band["last_sync"] is not None and 0 <= _wall() - band["last_sync"] < SYNC_COOLDOWN:
        return "recent"
    if _last_sync_request is not None and _monotonic() - _last_sync_request < SYNC_COOLDOWN:
        return "recent"
    return "sync"


def _pull(gb_running):
    decision = pull_decision(gb_running)
    if decision != "sync":
        return _json_response(200, "OK", {"ok": True, "action": PULL_ACTION, "sync": False, "reason": decision})
    _sync_requested()
    return _json_response(200, "OK", {"ok": True, "action": PULL_ACTION, "sync": True}, [[ACTION_HEADER, "sync"]])


def _android_api(method, path, headers, body):
    path = path.split("?", 1)[0]
    if path == "/api/android/band":
        if method != "GET":
            return _json_response(405, "Method Not Allowed", {"error": {"$": "err.invalid_request"}})
        return _json_response(200, "OK", band_status(headers.get(GB_PROCESS_HEADER) == "running"))
    if path == "/api/android/action":
        if method != "POST":
            return _json_response(405, "Method Not Allowed", {"error": {"$": "err.invalid_request"}})
        # same rules as Handler's POSTs: an external site cannot send this request
        if headers.get("x-helio") != "1" or not str(headers.get("content-type", "")).startswith("application/json"):
            return _json_response(403, "Forbidden", {"error": {"$": "err.forbidden"}})
        if not 0 < len(body) <= MAX_ACTION_BODY:
            return _json_response(400, "Bad Request", {"error": {"$": "err.invalid_request"}})
        try:
            data = json.loads(body)
        except ValueError:
            return _json_response(400, "Bad Request", {"error": {"$": "err.invalid_request"}})
        action = data.get("action") if isinstance(data, dict) else None
        if action == PULL_ACTION:
            return _pull(headers.get(GB_PROCESS_HEADER) == "running")
        if action not in ANDROID_ACTIONS:
            return _json_response(400, "Bad Request", {"error": {"$": "err.unknown_action"}})
        if action == "sync":
            _sync_requested()
        return _json_response(200, "OK", {"ok": True, "action": action}, [[ACTION_HEADER, action]])
    return _json_response(404, "Not Found", {"error": {"$": "err.not_found"}})
