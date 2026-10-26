"""Server tests: API, saving labels, errors, slow connections and HTTPS.

The server must never leave the phone without an answer: every error becomes a JSON
answer with a message, and a stalled connection does not block the others.
"""
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from test_helio import T, DAY, build_db, hm, load_app


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        build_db(cls.dir / "gb.db")
        cls.state = cls.dir / "state"
        cls.h = load_app(cls.dir / "gb.db", HELIO_STATE=str(cls.state))
        cls.srv = cls.h.ThreadingHTTPServer(("127.0.0.1", 0), cls.h.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.environ.pop("HELIO_STATE", None)
        cls.tmp.cleanup()

    def setUp(self):
        shutil.rmtree(self.state, ignore_errors=True)
        self.h._daycache.clear()

    def post(self, body, headers=None, raw=None, path="/api/label"):
        hd = {"Content-Type": "application/json", "X-Helio": "1"}
        hd.update(headers or {})
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=hd, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            txt = e.read()
            try:
                return e.code, json.loads(txt)
            except ValueError:
                return e.code, None

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def labels(self):
        f = self.state / "labels.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def shift(self, a=(14, 0), b=(20, 0), **kw):
        return {"start": hm(*a), "end": hm(*b), "type": "work", "manual": True, **kw}


class PostValidation(Server):
    def test_rejections_always_have_a_json_message(self):
        good = self.shift()
        cases = [
            ({}, {"X-Helio": ""}, None, 403),
            ({}, {"Content-Type": "text/plain"}, None, 403),
            (None, None, b"{non json", 400),
            (None, None, b"x" * 5000, 400),
            ({"end": hm(15, 0), "type": "work"}, None, None, 400),                   # start missing
            ({**good, "start": "ieri"}, None, None, 400),
            ({**good, "end": good["start"] - 60}, None, None, 400),                    # end before start
            ({**good, "end": good["start"]}, None, None, 400),
            ({**good, "type": "volo"}, None, None, 400),
            ({**good, "type": "strength", "day": 4}, None, None, 400),
            ({**good, "load_kg": -1}, None, None, 400),
            ({**good, "load_kg": 61}, None, None, 400),
            ({**good, "load_kg": "tanto"}, None, None, 400),
        ]
        for body, headers, raw, code in cases:
            got, payload = self.post(body, headers, raw)
            self.assertEqual(got, code, (body, headers, raw and raw[:20]))
            if code != 403 or payload is not None:
                self.assertIsInstance(payload, dict)
                self.assertTrue(payload.get("error"), (body, payload))
        self.assertEqual(self.labels(), {})

    def test_unknown_post_path(self):
        self.assertEqual(self.post({}, path="/api/qualcosa")[0], 404)

    def test_empty_body(self):
        self.assertEqual(self.post(None, raw=b"")[0], 400)


class SavingLabels(Server):
    def test_edit_manual_activity_twice_in_a_row(self):
        # the reported case: save, reopen Edit and save again
        self.assertEqual(self.post(self.shift()), (200, {"ok": True}))
        self.assertEqual(self.post(self.shift(type="other")), (200, {"ok": True}))
        self.assertEqual(self.post(self.shift(type="work", b=(21, 0))), (200, {"ok": True}))
        lb = self.labels()
        self.assertEqual(len(lb), 1)
        only = next(iter(lb.values()))
        self.assertEqual((only["type"], only["end"]), ("work", hm(21, 0)))
        work = [t for t in self.h.summary(DAY)["trainings"] if t["source"] == "manual"]
        self.assertEqual([(w["start"], w["end"]) for w in work], [(hm(14, 0), hm(21, 0))])

    def test_move_start_new_first_then_old_removed(self):
        self.post(self.shift())
        self.assertEqual(self.post(self.shift(a=(15, 0))), (200, {"ok": True}))
        self.assertEqual(len(self.labels()), 2)                   # for a moment both exist
        self.assertEqual(self.post({"start": hm(14, 0), "end": hm(14, 1), "type": None, "manual": True}), (200, {"ok": True}))
        lb = self.labels()
        self.assertEqual([v["start"] for v in lb.values()], [hm(15, 0)])

    def test_remove_missing_label_is_harmless(self):
        self.assertEqual(self.post({"start": hm(3, 0), "end": hm(3, 1), "type": None, "manual": True}), (200, {"ok": True}))
        self.assertEqual(self.labels(), {})

    def test_label_change_is_visible_immediately(self):
        s1 = self.h.summary(DAY)
        w = s1["trainings"][0]
        self.post({"start": w["start"], "end": w["end"], "type": "strength", "day": 1})
        code, body = self.get(f"/api/summary?date={DAY.isoformat()}")
        self.assertEqual(code, 200)
        self.assertEqual(T(json.loads(body)["trainings"][0]["name"]), "Strength training · day 1")
        self.post({"start": w["start"], "end": w["end"], "type": "walk"})
        self.assertEqual(T(json.loads(self.get(f"/api/summary?date={DAY.isoformat()}")[1])["trainings"][0]["name"]), "Walking")
        self.assertEqual(len(self.labels()), 1)                  # same session: replaced, not duplicated

    def test_concurrent_saves_are_not_lost(self):
        results = []

        def save(i):
            results.append(self.post(self.shift(a=(10, i), b=(10, i + 1)))[0])

        threads = [threading.Thread(target=save, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [200] * 12)
        self.assertEqual(len(self.labels()), 12)
        self.assertFalse((self.state / "labels.tmp").exists())

    def test_corrupt_labels_file_does_not_break_anything(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "labels.json").write_text("{tronc")
        self.assertEqual(self.get(f"/api/summary?date={DAY.isoformat()}")[0], 200)
        self.assertEqual(self.post(self.shift()), (200, {"ok": True}))
        self.assertEqual(len(self.labels()), 1)

    def test_labels_file_not_a_dict(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "labels.json").write_text("[1, 2]")
        self.assertEqual(self.get(f"/api/summary?date={DAY.isoformat()}")[0], 200)
        self.assertEqual(self.post(self.shift())[0], 200)

    def test_unwritable_state_gives_a_message(self):
        if os.geteuid() == 0:
            self.skipTest("as root, permissions do not block writing")
        self.state.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state, 0o500)
        try:
            code, body = self.post(self.shift())
            self.assertEqual(code, 500)
            self.assertIn("could not save", T(body["error"]))
        finally:
            os.chmod(self.state, 0o700)

    def test_unexpected_error_still_answers(self):
        orig = self.h.save_label
        self.h.save_label = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            code, body = self.post(self.shift())
            self.assertEqual(code, 500)
            self.assertEqual(T(body["error"]), "internal error, try again")
        finally:
            self.h.save_label = orig

    def test_database_error_still_answers(self):
        orig = self.h.DB
        self.h.DB = str(self.dir / "non-esiste.db")
        try:
            code, body = self.post(self.shift())
            self.assertEqual(code, 500)
            self.assertIn("could not save", T(body["error"]))
        finally:
            self.h.DB = orig

    def test_overnight_shift(self):
        a = hm(22, 0)
        self.assertEqual(self.post({"start": a, "end": a + 8 * 3600, "type": "work", "manual": True}), (200, {"ok": True}))
        w = [t for t in self.h.summary(DAY)["trainings"] if t["source"] == "manual"]
        self.assertEqual(w[0]["duration_min"], 480)


class LoadOnlyWhereItMakesSense(Server):
    """The added load applies to walking, hiking and strength; for the other types it is reset."""

    def first(self):
        return self.h.summary(DAY)["trainings"][0]

    def test_switch_from_weighted_strength_to_swimming(self):
        w = self.first()
        self.post({"start": w["start"], "end": w["end"], "type": "strength", "day": 2, "load_kg": 15})
        self.assertEqual(T(self.first()["label"])["load_kg"], 15)
        # the old page still sent 15 kg for swimming too
        self.assertEqual(self.post({"start": w["start"], "end": w["end"], "type": "swim", "load_kg": 15}), (200, {"ok": True}))
        t = self.first()
        self.assertEqual((T(t["name"]), T(t["label"])["type"], T(t["label"])["load_kg"], T(t["label"])["day"]), ("Swimming", "swim", 0, None))
        self.assertNotIn("kg", T(t["kcal_method"]))
        self.assertEqual(next(iter(self.labels().values()))["load_kg"], 0)

    def test_old_saved_labels_are_corrected_when_read(self):
        w = self.first()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "labels.json").write_text(json.dumps({str(w["start"]): {
            "start": w["start"], "end": w["end"], "type": "swim", "day": 2, "load_kg": 15, "features": {}}}))
        t = self.first()
        self.assertEqual((T(t["label"])["load_kg"], T(t["label"])["day"]), (0, None))
        self.assertEqual(T(t["name"]), "Swimming")

    def test_every_type(self):
        w = self.first()
        for typ in self.h.ACTIVITY_TYPES:
            body = {"start": w["start"], "end": w["end"], "type": typ, "load_kg": 12}
            if typ == "strength":
                body["day"] = 1
            self.assertEqual(self.post(body)[0], 200, typ)
            expected = 12 if typ in ("walk", "hike", "strength") else 0
            self.assertEqual(T(self.first()["label"])["load_kg"], expected, typ)

    def test_load_changes_walking_calories_only_when_kept(self):
        w = self.first()
        self.post({"start": w["start"], "end": w["end"], "type": "walk", "load_kg": 10})
        self.assertIn("Pandolf", T(self.first()["kcal_method"]))
        self.post({"start": w["start"], "end": w["end"], "type": "run", "load_kg": 10})
        t = self.first()
        self.assertNotIn("Pandolf", T(t["kcal_method"]))
        self.assertEqual(t["kcal"], t["kcal_default"])

    def test_manual_activity_ignores_load(self):
        self.post(self.shift(load_kg=8))
        self.assertEqual(next(iter(self.labels().values()))["load_kg"], 0)

    def test_page_knows_which_types_use_load(self):
        types = {t["key"]: t["load"] for t in self.h.summary(DAY)["activity_types"]}
        self.assertEqual({k for k, v in types.items() if v}, {"walk", "hike", "strength"})

    def test_label_load_helper(self):
        f = self.h.label_load
        self.assertEqual(f("strength", 15), 15)
        self.assertEqual(f("strength", None), 0)
        self.assertEqual(f("swim", 15), 0)
        self.assertEqual(f("sconosciuto", 15), 0)


class GetEndpoints(Server):
    def test_every_endpoint_with_odd_dates(self):
        for ep in ("summary?date=", "trends?end=", "vitals?end=", "nutrition?date="):
            for d in ("", "2026-02-30", "abc", "1999-01-01", "2100-01-01", DAY.isoformat()):
                code, body = self.get(f"/api/{ep}{d}")
                self.assertEqual(code, 200, (ep, d))
                json.loads(body)
        code, body = self.get("/api/foods")
        self.assertEqual((code, json.loads(body)["foods"]), (200, []))

    def test_unknown_api(self):
        self.assertEqual(self.get("/api/niente")[0], 404)

    def test_handler_has_a_timeout(self):
        self.assertTrue(0 < self.h.Handler.timeout <= 120)

    def test_stalled_client_does_not_block_others(self):
        s = socket.create_connection(self.srv.server_address)
        s.sendall(b"POST /api/label HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{")  # body never finished
        try:
            t = time.time()
            self.assertEqual(self.get(f"/api/summary?date={DAY.isoformat()}")[0], 200)
            self.assertLess(time.time() - t, 5)
        finally:
            s.close()


@unittest.skipUnless(shutil.which("openssl"), "openssl not available")
class Https(unittest.TestCase):
    """Home network: a half-open TLS connection must not block the app."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        build_db(d / "gb.db")
        cert, key = d / "c.pem", d / "k.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost"],
                       check=True, capture_output=True)
        cls.h = load_app(d / "gb.db", HELIO_STATE=str(d / "state"), HELIO_LAN_OPEN="1")
        cls.srv = cls.h.TLSServer(("127.0.0.1", 0), cls.h.LanHandler, cls.h.tls_context(str(cert), str(key)))
        cls.srv.handshake_timeout = 3
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]
        cls.client = ssl.create_default_context(cafile=str(cert))
        cls.client.check_hostname = False

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.environ.pop("HELIO_STATE", None)
        os.environ.pop("HELIO_LAN_OPEN", None)
        cls.tmp.cleanup()

    def request(self, method="GET", path=f"/api/summary?date={DAY.isoformat()}", body=None):
        req = urllib.request.Request(f"https://127.0.0.1:{self.port}{path}", data=body, method=method,
                                     headers={"Content-Type": "application/json", "X-Helio": "1"})
        with urllib.request.urlopen(req, timeout=5, context=self.client) as r:
            return r.status, r.read()

    def test_https_works(self):
        self.assertEqual(self.request()[0], 200)

    def test_half_open_connection_does_not_block(self):
        idle = [socket.create_connection(("127.0.0.1", self.port)) for _ in range(3)]  # no handshake
        try:
            t = time.time()
            self.assertEqual(self.request()[0], 200)
            body = json.dumps({"start": hm(14, 0), "end": hm(15, 0), "type": "work", "manual": True}).encode()
            self.assertEqual(self.request("POST", "/api/label", body)[0], 200)
            self.assertLess(time.time() - t, 3)
        finally:
            for s in idle:
                s.close()

    def test_plain_http_on_https_port_is_ignored(self):
        s = socket.create_connection(("127.0.0.1", self.port))
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        s.close()
        self.assertEqual(self.request()[0], 200)


class PlanRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        build_db(d / "gb.db")
        self.plan = d / "plan.json"
        self.h = load_app(d / "gb.db", HELIO_PLAN=str(self.plan), HELIO_STATE=str(d / "state"))

    def tearDown(self):
        os.environ.pop("HELIO_STATE", None)
        os.environ.pop("HELIO_PLAN", None)
        self.tmp.cleanup()

    def test_missing_or_broken_plan(self):
        for content in (None, "", "{rotto", "[]", '{"2": 5}', '{"2": {"types": "strength"}}', '{"2": {"day": "x", "types": ["strength"]}}'):
            if content is None:
                self.plan.unlink(missing_ok=True)
            else:
                self.plan.write_text(content)
            self.h._daycache.clear()
            p = self.h.plan_for(DAY)
            self.assertIsInstance(T(p["text"]), str, content)
            self.assertIsInstance(p["types"], list, content)
            json.dumps(self.h.summary(DAY))

    def test_unknown_types_in_plan_are_ignored(self):
        self.plan.write_text(json.dumps({"2": {"text": "Volo", "types": ["volo", "strength"], "day": 2}}))
        p = self.h.plan_for(DAY)  # DAY is a Tuesday
        self.assertEqual((p["types"], p["day"]), (["strength"], 2))

    def test_text_plan_words(self):
        self.plan.write_text(json.dumps({"2": "Strength training day 3 then rope"}))
        self.assertEqual(self.h.plan_for(DAY)["day"], 3)
        self.plan.write_text(json.dumps({"2": "Allenamento di forza giorno 3 poi corda"}))  # lang: it
        p = self.h.plan_for(DAY)
        self.assertEqual(p["day"], 3)
        self.assertIn("strength", p["types"])
        self.assertIn("rope", p["types"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
