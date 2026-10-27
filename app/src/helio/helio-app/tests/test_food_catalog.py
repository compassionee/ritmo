"""The food catalog inside the app: the user's data is never touched by the catalog (foods.json identical byte for
byte after installing, updating, failing to update and removing it), search and barcodes never use the network,
community foods in the diary without saving them, "Save to my foods" as a copy, missing optional nutrients
("at least X g"), export and import of the user's data without the catalog, update from a URL."""
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from test_helio import DAY, T, build_db, load_app
from test_catalog import build, repack, sample_generic, sample_off

import helio_catalog as hc
import helio_transfer

APP_DIR = Path(__file__).resolve().parent.parent

# foods.json written by hand, with an unusual layout: any rewrite would show up
FOODS_JSON = """{"meals": {"m1": {"name": "Colazione tipo", "meal": "breakfast", "items": [["f1", 40], ["r1", 100]],
  "id": "m1"}},
    "foods": {
        "f1": {"kind": "food", "name": "Oats", "id": "f1", "portions": [["1 cup", 40]],
               "per100": {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7.0, "fiber": 10.0}, "updated": 1,
               "note": "a field this version does not know"},
        "r1": {"kind": "recipe", "name": "Porridge", "id": "r1", "portions": [], "items": [["f1", 50]],
               "cooked_g": 250, "updated": 2},
        "broken": {"kind": "food", "name": "", "per100": {}}
    }
}
"""  # lang: it


def snapshot(folder):
    """Every file of a folder: bytes, SHA-256 and modification time (ns)."""
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            out[str(p.relative_to(folder))] = (data, hashlib.sha256(data).hexdigest(), p.stat().st_mtime_ns)
    return out


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        build_db(cls.tmp / "gb.db")
        cls.state = cls.tmp / "state"
        cls.cat = cls.tmp / "catalog"
        cls.pkgs = cls.tmp / "packages"
        cls.pkgs.mkdir()
        cls.generic = build(cls.pkgs, sample_generic())
        cls.off1 = build(cls.pkgs, sample_off(), kind="off", version="2026-10-21")
        cls.off2 = build(cls.pkgs, sample_off()[:2], kind="off", version="2026-11-21")
        cls.h = load_app(cls.tmp / "gb.db", HELIO_STATE=str(cls.state), HELIO_CATALOG=str(cls.cat),
                         HELIO_CATALOG_BUNDLED=str(cls.generic))

    @classmethod
    def tearDownClass(cls):
        for k in ("HELIO_STATE", "HELIO_CATALOG", "HELIO_CATALOG_BUNDLED"):
            os.environ.pop(k, None)
        cls._tmp.cleanup()

    def setUp(self):
        shutil.rmtree(self.state, ignore_errors=True)
        shutil.rmtree(self.cat, ignore_errors=True)
        self.state.mkdir()
        (self.state / "foods.json").write_text(FOODS_JSON)
        self.h._catalog_checked = False
        self.h._daycache.clear()

    def raw_foods(self):
        return json.loads((self.state / "foods.json").read_text())


class UserDataUntouched(Base):
    def test_foods_json_identical_after_install_update_and_remove(self):
        h = self.h
        before = snapshot(self.state)
        steps = [
            ("bundled catalog", lambda: h.catalog_info()),
            ("search", lambda: h.catalog_search("mela", "it")),
            ("barcode", lambda: h.catalog_barcode("8001234567890")),
            ("install", lambda: h.catalog_install(self.off1)),
            ("search after install", lambda: h.catalog_search("biscotti", "it")),
            ("update", lambda: h.catalog_install(self.off2)),
            ("same version again", lambda: h.catalog_install(self.off2)),
        ]
        for name, step in steps:
            step()
            self.assertEqual(snapshot(self.state), before, name)
        self.assertEqual(hc.info(self.cat)["off"]["version"], "2026-11-21")
        bad = repack(self.off1, self.pkgs / "bad.tar", manifest={**hc.read_manifest(self.off1), "xz_sha256": "0" * 64})
        for name, pkg in (("bad hash", bad), ("not a package", self.state / "foods.json")):
            with self.assertRaises(self.h.UserError):
                h.catalog_install(pkg)
            self.assertEqual(snapshot(self.state), before, name)
        self.assertEqual(hc.info(self.cat)["off"]["version"], "2026-11-21")  # the failed update changed nothing
        h.catalog_op({"op": "remove", "kind": "off"})
        self.assertEqual(snapshot(self.state), before, "remove")
        self.assertEqual(set(hc.info(self.cat)), {"generic"})
        self.assertEqual((self.state / "foods.json").read_text(), FOODS_JSON)

    def test_bundled_catalog_installs_next_to_the_data(self):
        self.h.catalog_search("mela")
        self.assertTrue((self.cat / "generic.sqlite").is_file())
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["foods.json"])

    def test_default_catalog_folder_is_a_sibling(self):
        h = load_app(self.tmp / "gb.db", HELIO_STATE=str(self.state))
        try:
            self.assertEqual(h.CATALOG_DIR, self.tmp / "state-catalog")
            self.assertEqual(h.CATALOG_BUNDLED, str(APP_DIR / "catalog" / "generic.tar"))
        finally:
            type(self).h = load_app(self.tmp / "gb.db", HELIO_STATE=str(self.state), HELIO_CATALOG=str(self.cat),
                                    HELIO_CATALOG_BUNDLED=str(self.generic))

    def test_catalog_inside_the_data_is_refused(self):
        with mock.patch.object(self.h, "CATALOG_DIR", self.state / "catalog"):
            with self.assertRaises(self.h.UserError) as e:
                self.h.catalog_install(self.off1)
            self.assertEqual(e.exception.msg["$"], "cat.e.dir")
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["foods.json"])

    def test_only_the_community_catalog_is_replaced_by_hand(self):
        with self.assertRaises(self.h.UserError):
            self.h.catalog_install(self.generic)
        with self.assertRaises(self.h.UserError):
            self.h.catalog_op({"op": "remove", "kind": "generic"})

    def test_export_and_import_ignore_the_catalog(self):
        h = self.h
        before = helio_transfer.state_files(self.state)
        h.catalog_install(self.off1)
        self.assertEqual(helio_transfer.state_files(self.state), before)
        archive = helio_transfer.export_bytes(str(self.state))
        _, files = helio_transfer.read_archive(archive)
        self.assertEqual(set(files), {"foods.json"})
        self.assertNotIn(b"SQLite", b"".join(files.values()))
        cat_before = snapshot(self.cat)
        helio_transfer.import_bytes(archive, str(self.state))
        self.assertEqual(snapshot(self.cat), cat_before)  # importing the user's data leaves the catalog alone
        self.assertEqual((self.state / "foods.json").read_text(), FOODS_JSON)


class SaveToMyFoods(Base):
    def item(self, q="branzino"):
        return self.h.catalog_search(q, "it")["community"][0]

    def test_copy_is_added_without_touching_the_others(self):
        before = self.raw_foods()
        it = self.item()
        res = self.h.save_food({"food": {"name": "Branzino", "per100": it["per100"], "portions": [],
                                         "origin": {"src": it["src"], "ref": it["ref"]}}})
        after = self.raw_foods()
        new = after["foods"].pop(res["id"])
        self.assertEqual(after, before)  # every other entry (also the unreadable one) exactly as before
        self.assertEqual(new["origin"]["src"], "usda_sr")
        self.assertEqual(new["origin"]["v"], it["per100"])
        self.assertNotIn("fiber", new["per100"])  # missing in the source: stays missing, not 0
        pay = {f["id"]: f for f in self.h.foods_payload()["foods"]}[res["id"]]
        self.assertEqual(set(pay["src"].values()), {"usda_sr"})
        self.assertEqual(pay["missing"], ["fiber"])
        self.assertEqual(pay["origin"], {"src": "usda_sr", "ref": "175142", "version": "2026-10-21"})

    def test_values_claimed_by_the_page_are_not_the_source(self):
        it = self.item()
        fake = {**it["per100"], "protein": 30}
        res = self.h.save_food({"food": {"name": "Branzino", "per100": fake,
                                         "origin": {"src": it["src"], "ref": it["ref"], "v": fake}}})
        pay = {f["id"]: f for f in self.h.foods_payload()["foods"]}[res["id"]]
        self.assertEqual(pay["src"]["protein"], "user")  # different from the catalog: the user's value
        self.assertEqual(pay["src"]["fat"], "usda_sr")

    def test_correcting_a_copy_marks_the_value_as_mine(self):
        self.h.catalog_install(self.off1)
        it = self.item("biscotti")
        fid = self.h.save_food({"food": {"name": "Biscotti", "per100": it["per100"], "barcode": it["barcode"],
                                         "origin": {"src": "off", "ref": it["ref"]}}})["id"]
        self.h.save_food({"food": {"id": fid, "name": "Biscotti", "per100": {**it["per100"], "salt": 0.7}}})
        f = self.h.foods_load()[0][fid]
        self.assertEqual(f["origin"]["ref"], "8001234567890")  # origin and barcode survive the edit
        self.assertEqual(f["barcode"], "8001234567890")
        pay = {x["id"]: x for x in self.h.foods_payload()["foods"]}[fid]
        self.assertEqual((pay["src"]["salt"], pay["src"]["fat"]), ("user", "off"))
        mine = self.h.catalog_barcode("8001234567890")
        self.assertEqual([x["id"] for x in mine["mine"]], [fid])  # my copy first
        self.assertEqual(len(mine["community"]), 1)  # the community product stays, nothing is merged

    def test_same_name_is_refused_and_nothing_changes(self):
        it = self.item()
        before = (self.state / "foods.json").read_bytes()
        with self.assertRaises(self.h.UserError) as e:
            self.h.save_food({"food": {"name": "Oats", "per100": it["per100"], "origin": {"src": it["src"], "ref": it["ref"]}}})
        self.assertEqual(e.exception.msg["$"], "e.food_exists")
        self.assertEqual((self.state / "foods.json").read_bytes(), before)

    def test_unknown_origin(self):
        with self.assertRaises(self.h.UserError):
            self.h.save_food({"food": {"name": "X", "per100": {"protein": 1, "carbs": 1, "fat": 1},
                                       "origin": {"src": "off", "ref": "0000"}}})

    def test_fibre_still_required_without_an_origin(self):
        with self.assertRaises(self.h.UserError):
            self.h.save_food({"food": {"name": "X", "per100": {"protein": 1, "carbs": 1, "fat": 1}}})

    def test_my_foods_first_in_the_search(self):
        r = self.h.catalog_search("oat", "en")
        self.assertEqual([x["name"] for x in r["mine"]], ["Oats"])
        self.assertEqual(self.h.catalog_search("colaz", "it")["mine"][0]["type"], "meal")
        self.assertEqual(self.h.catalog_search("porr", "it")["mine"][0]["type"], "recipe")
        it = self.h.catalog_search("mela", "it")["community"][0]
        self.assertEqual((it["name"], it["short"], it["src"]), ("Mela", "Ciqual", "ciqual"))


class BarcodesAndVersions(Base):
    def test_my_copy_found_by_every_form_of_its_code(self):
        self.h.catalog_install(self.off1)
        it = self.h.catalog_barcode("20570910")["community"]
        bis = next(x for x in it if x["ref"] == "20570910")
        fid = self.h.save_food({"food": {"name": "Bisfrolla", "per100": bis["per100"], "barcode": bis["barcode"],
                                         "origin": {"src": "off", "ref": "20570910"}}})["id"]
        for code in ("20570910", "0000020570910", "00000020570910"):
            r = self.h.catalog_barcode(code)
            self.assertEqual([x["id"] for x in r["mine"]], [fid], code)
            self.assertEqual(len(r["community"]), 2)  # two different products share this store code
            self.assertEqual(r["code"], "20570910")
        with self.assertRaises(self.h.UserError):
            self.h.catalog_barcode("1234")

    def test_catalog_of_the_previous_version_must_be_imported_again(self):
        self.cat.mkdir(parents=True, exist_ok=True)
        (self.cat / "off.sqlite").write_bytes(b"SQLite format 3\x00 schema 1")  # installed by versionCode 261
        info = self.h.catalog_info()
        self.assertNotIn("off", info["installed"])
        self.assertEqual(info["outdated"], ["off"])
        self.assertEqual(self.h.catalog_search("biscotti", "it")["community"], [])  # never opened
        self.h.catalog_install(self.off1)
        self.assertEqual(self.h.catalog_info()["outdated"], [])

    def test_speed_test_runs_locally(self):
        self.h.catalog_install(self.off1)
        r = self.h.catalog_bench("it")
        self.assertEqual(len(r["queries"]), 13)
        self.assertTrue(all(q["ms"] >= 0 for q in r["queries"]))
        self.assertLessEqual(r["median_ms"], r["max_ms"])


class DiaryFromCatalog(Base):
    def test_add_without_saving(self):
        h = self.h
        foods_before = (self.state / "foods.json").read_bytes()
        it = h.catalog_search("branzino", "it")["community"][0]
        h.diary_op({"date": DAY.isoformat(), "op": "add", "meal": "dinner", "g": 200,
                    "catalog": {"src": it["src"], "ref": it["ref"]}, "lang": "it"})
        apple = h.catalog_search("mela", "it")["community"][0]
        h.diary_op({"date": DAY.isoformat(), "op": "add", "meal": "lunch", "g": 150,
                    "catalog": {"src": apple["src"], "ref": apple["ref"]}, "lang": "it"})
        self.assertEqual((self.state / "foods.json").read_bytes(), foods_before)
        e = h.diary_days(DAY, DAY)[DAY.isoformat()]["entries"]
        self.assertEqual([x["name"] for x in e], ["Branzino", "Mela"])
        self.assertIsNone(e[0]["food"])
        self.assertEqual(e[0]["src"], {"src": "usda_sr", "ref": "175142", "version": "2026-10-21"})
        n = h.nutrition_day(DAY)
        self.assertEqual(n["missing"]["fiber"]["n"], 1)
        self.assertEqual(n["missing"]["fiber"]["names"], ["Branzino"])
        fish = sample_generic()[2]["values"]
        self.assertAlmostEqual(n["totals"]["kcal"], round(fish["kcal"] * 2 + sample_generic()[0]["values"]["kcal"] * 1.5, 1),
                               places=0)  # energy and macronutrients unchanged by the missing fibre
        self.assertAlmostEqual(n["totals"]["fiber"], 1.4 * 1.5, places=1)
        self.assertFalse(n["complete"])
        srcs = [x["src"]["src"] for m in n["meals"] for x in m["entries"]]
        self.assertEqual(sorted(srcs), ["ciqual", "usda_sr"])
        h.diary_op({"date": DAY.isoformat(), "op": "complete", "complete": True})
        chk = {c["key"]: c for c in h.nutrition_day(DAY)["day_checks"]}["fiber"]
        self.assertEqual(chk["status"], "info")  # only a minimum: no judgement below the reference
        self.assertEqual(T(chk["display"]), "at least 2 g (1 food without the value)")
        self.assertIn("Branzino", T(chk["text"]))
        self.assertTrue(h.nutrition_day(DAY)["complete"])  # the day stays complete

    def test_unknown_catalog_food(self):
        with self.assertRaises(self.h.UserError):
            self.h.diary_op({"date": DAY.isoformat(), "op": "add", "meal": "dinner", "g": 100,
                             "catalog": {"src": "off", "ref": "nope"}})


class NoNetwork(Base):
    def test_search_and_barcode_do_not_touch_the_network(self):
        attempts = []

        def refuse(*a, **kw):
            attempts.append(a)
            raise OSError("network blocked by the test")

        self.h.catalog_install(self.off1)
        with mock.patch.object(socket.socket, "connect", refuse), \
                mock.patch.object(socket.socket, "connect_ex", refuse), \
                mock.patch.object(socket, "create_connection", refuse), \
                mock.patch.object(socket, "getaddrinfo", refuse), \
                mock.patch.object(urllib.request, "urlopen", refuse):
            for q in ("mela", "bisc", "caffe esp", "marca", "oats", "8001234567890"):
                self.h.catalog_search(q, "it")
                self.h.catalog_search(q, "en")
            self.h.catalog_barcode("8001234567890")
            self.h.catalog_barcode("012345678905")
            self.h.catalog_info()
            it = self.h.catalog_search("biscotti", "it")["community"][0]
            self.h.diary_op({"date": DAY.isoformat(), "op": "add", "meal": "lunch", "g": 30,
                             "catalog": {"src": it["src"], "ref": it["ref"]}})
            self.h.save_food({"food": {"name": "Biscotti", "per100": it["per100"], "origin": {"src": "off", "ref": it["ref"]}}})
            self.h.nutrition_day(DAY)
            self.h.foods_payload()
            self.h.catalog_bench("it")
        self.assertEqual(attempts, [])

    def test_no_network_code_in_the_app(self):
        # Ritmo has no network permission (from 266): no Python module of the app is a network client, and the
        # old download module of "Update from URL" is gone. helio_app's http.server is the local server.
        self.assertFalse((APP_DIR / "helio_catalog_fetch.py").exists())
        clients = ("urllib.request", "urllib.error", "http.client", "ftplib", "smtplib", "poplib", "imaplib",
                   "telnetlib", "xmlrpc", "requests", "httpx", "aiohttp", "helio_catalog_fetch")
        files = sorted(APP_DIR.glob("*.py"))
        bridge = APP_DIR.parent / "python" / "helio_android.py"  # in the Gadgetbridge flavor
        files += [bridge] if bridge.is_file() else []
        self.assertIn("helio_catalog.py", [f.name for f in files])
        for f in files:
            src = f.read_text()
            for mod in clients:
                self.assertNotRegex(src, rf"(?m)^\s*(import|from)\s+{re.escape(mod)}\b", f"{f.name}: {mod}")
            self.assertNotRegex(src, r"create_connection|urlopen|\.connect\(\(", f.name)
        src = (APP_DIR / "helio_catalog.py").read_text()
        for mod in ("socket", "ssl", "http", "urllib"):
            self.assertNotRegex(src, rf"(?m)^\s*(import|from)\s+{mod}\b", mod)


class NoUpdateFromUrl(Base):
    """"Update from URL" is gone (Ritmo has no network): only "Import from file" installs a package."""

    def test_url_operations_are_refused_and_write_nothing(self):
        before = snapshot(self.cat) if self.cat.exists() else {}
        for body in ({"op": "set_url", "url": "https://example.org/x.tar"}, {"op": "update_url"}):
            with self.assertRaises(self.h.UserError) as e:
                self.h.catalog_op(body)
            self.assertEqual(e.exception.msg["$"], "e.op_invalid")
        self.assertEqual(snapshot(self.cat) if self.cat.exists() else {}, before)
        self.assertNotIn("url", self.h.catalog_info())
        for name in ("catalog_url", "catalog_update_url", "CATALOG_URL_FILE"):
            self.assertFalse(hasattr(self.h, name), name)

    def test_address_saved_by_an_older_version_is_removed(self):
        self.cat.mkdir(parents=True, exist_ok=True)
        (self.cat / "source.json").write_text('{"url": "https://example.org/x.tar"}')
        self.h._catalog_checked = False
        info = self.h.catalog_info()
        self.assertNotIn("url", info)
        self.assertFalse((self.cat / "source.json").exists())

    def test_no_texts_of_the_url_update(self):
        for lang in ("en", "it"):
            cat = json.loads((APP_DIR / "static" / "i18n" / f"{lang}.json").read_text())
            self.assertEqual([k for k in cat if k.startswith(("cat.url", "cat.update_url", "cat.e.url", "cat.e.no_url",
                                                               "cat.e.download"))], [], lang)
            self.assertNotIn("URL", cat["cat.server_import"], lang)
        html = (APP_DIR / "static" / "index.html").read_text()
        js = (APP_DIR / "static" / "app.js").read_text()
        for text in ("catalogUrl", "set_url", "update_url"):
            self.assertNotIn(text, html + js, text)


class Api(Base):
    """The routes, through the request handler."""

    def request(self, method, path, body=None):
        import io

        class Conn:
            def __init__(self, raw):
                self.i, self.out = io.BytesIO(raw), bytearray()

            def makefile(self, mode, *a):
                return self.i

            def sendall(self, b):
                self.out += b

            def settimeout(self, t):
                pass

        data = json.dumps(body).encode() if body is not None else b""
        head = f"{method} {path} HTTP/1.1\r\nHost: x\r\nX-Helio: 1\r\nContent-Type: application/json\r\n" \
               f"Content-Length: {len(data)}\r\n\r\n"
        c = Conn(head.encode() + data)
        with mock.patch.object(self.h.Handler, "_authorized", return_value=True), \
                mock.patch.object(self.h.Handler, "finish", lambda s: None):
            self.h.Handler(c, ("127.0.0.1", 1), None)
        raw = bytes(c.out)
        status = int(raw.split(b" ", 2)[1])
        return status, json.loads(raw.split(b"\r\n\r\n", 1)[1] or b"null")

    def test_routes(self):
        st, r = self.request("GET", "/api/catalog/search?q=mela&lang=it")
        self.assertEqual(st, 200)
        self.assertEqual(r["community"][0]["name"], "Mela")
        st, r = self.request("GET", "/api/catalog/barcode?code=abc")
        self.assertEqual((st, r["error"]["$"]), (400, "e.barcode_invalid"))
        st, r = self.request("GET", "/api/catalog/info")
        self.assertEqual(set(r["installed"]), {"generic"})
        self.assertEqual(r["sources"]["off"]["license"], "ODbL 1.0")
        self.assertNotIn("url", r)
        # the "Update from URL" operations no longer exist
        for body in ({"op": "set_url", "url": "https://x"}, {"op": "update_url"}):
            st, r = self.request("POST", "/api/catalog", body)
            self.assertEqual((st, r["error"]["$"]), (400, "e.op_invalid"))


if __name__ == "__main__":
    unittest.main()
