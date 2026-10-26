"""Profile sheet in the app: requests go through the bridge as on the server and the profile stays in
the app's data folder, with the same export format."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HELIO_SRC = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HELIO_SRC / "python"), str(HELIO_SRC / "helio-app"), str(HELIO_SRC / "helio-app" / "tests")]

import helio_android  # noqa: E402
from test_helio import D0, T, build_db  # noqa: E402

JSON = {"Content-Type": "application/json", "X-Helio": "1"}


class ProfileThroughTheBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "gb.db"
        build_db(self.db)
        os.environ["HELIO_NOW"] = str(D0 + 23 * 3600)
        self.state = self.dir / "files" / "helio-state"
        self.profile_path = self.dir / "files" / "helio-config.json"
        self.h = helio_android.configure(str(self.db), str(self.state), None, "Europe/Rome",
                                         profile_path=str(self.profile_path))

    def tearDown(self):
        for k in ("HELIO_NOW", "HELIO_DB_LIVE", "HELIO_STATE", "HELIO_PLAN", "HELIO_DEVICE_ID", "HELIO_TZ"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def post(self, body, headers=JSON):
        r = helio_android.handle("POST", "/api/profile", headers, json.dumps(body).encode())
        return r[0], json.loads(r[3])

    def get(self):
        r = helio_android.handle("GET", "/api/profile", {})
        return r[0], json.loads(r[3])

    def test_save_read_and_recompute(self):
        self.assertEqual(self.post({"profile": {"height_cm": 166, "hrmax": 181, "steps_goal": 7000}}), (200, {"ok": True}))
        st, body = self.get()
        self.assertEqual(st, 200)
        self.assertEqual(body["saved"], {"height_cm": 166.0, "hrmax": 181, "steps_goal": 7000})
        s = json.loads(helio_android.handle("GET", "/api/summary", {})[3])
        self.assertEqual((s["profile"]["height"], s["profile"]["hrmax"], s["goals"]["steps"]), (166.0, 181, 7000))

    def test_only_in_the_app_data(self):
        self.post({"profile": {"height_cm": 166}})
        self.assertEqual(json.loads((self.state / "profile.json").read_text()), {"height_cm": 166.0})
        self.assertFalse(self.profile_path.exists())  # the server profile (helio-config.json) does not change

    def test_same_checks_as_the_server(self):
        self.assertEqual(self.post({"profile": {"height_cm": 172}}, {"Content-Type": "application/json"})[0], 403)
        st, body = self.post({"profile": {"rest_hr": 150}})
        self.assertEqual(st, 400)
        self.assertIn("resting HR outside the allowed range (30-110 bpm)", T(body["error"]))
        self.assertFalse((self.state / "profile.json").exists())

    def test_export_contains_the_profile(self):
        import helio_transfer as ht
        self.post({"profile": {"sex": "F"}})
        _, files = ht.read_archive(helio_android.export_state(str(self.state), str(self.profile_path)))
        self.assertEqual(json.loads(files["profile.json"]), {"sex": "F"})


if __name__ == "__main__":
    unittest.main()
