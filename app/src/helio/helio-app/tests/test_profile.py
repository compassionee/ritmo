"""Profile sheet: values entered by hand, checked, saved only in the app's data (profile.json),
taking precedence over configuration and Gadgetbridge and used at once in every calculation."""
import json
import math
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_helio import T, DAY, build_db, load_app  # noqa: E402


class Base(unittest.TestCase):
    env = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "gb.db"
        build_db(self.db)
        self.state = self.dir / "state"
        self.h = load_app(self.db, HELIO_STATE=str(self.state), **self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def prof(self, d=None):
        with closing(self.h.connect()) as con:
            return self.h.profile(con, d or DAY)

    def save(self, **values):
        return self.h.save_profile({"profile": values})

    def born(self, years):
        """Birth date for an exact age today (by the app's clock)."""
        t = self.h.now().date()
        return date(t.year - years, 1, 1).isoformat() if (t.month, t.day) >= (1, 1) else None


class Validation(Base):
    def test_values_inside_the_ranges(self):
        for key, (cast, lo, hi, _, _) in self.h.PROFILE_NUMBERS.items():
            for v in (lo, hi, (lo + hi) / 2 if cast is float else (lo + hi) // 2):
                self.assertEqual(self.h.validate_profile({key: v}), {key: cast(v) if cast is int else round(v, 2)}, key)

    def test_values_outside_the_ranges(self):
        for key, (cast, lo, hi, unit, name) in self.h.PROFILE_NUMBERS.items():
            step = 1 if cast is int else 0.01
            for v in (lo - step, hi + step, -lo):
                with self.assertRaises(ValueError, msg=f"{key}={v}") as cm:
                    self.h.validate_profile({key: v})
                self.assertEqual(cm.exception.msg["field"], {"$": name})
                self.assertIn("allowed range", T(cm.exception.msg))

    def test_wrong_types(self):
        for key in self.h.PROFILE_NUMBERS:
            for v in (True, "170", [170], {"v": 1}, math.nan, math.inf):
                with self.assertRaises(ValueError, msg=f"{key}={v!r}"):
                    self.h.validate_profile({key: v})
        with self.assertRaises(ValueError):
            self.h.validate_profile({"hrmax": 180.5})  # whole-number HR
        for bad in (None, [], "x", 3):
            with self.assertRaises(ValueError):
                self.h.validate_profile(bad)

    def test_sex_and_unknown_fields(self):
        self.assertEqual(self.h.validate_profile({"sex": "F"}), {"sex": "F"})
        for v in ("f", "X", 1, True):
            with self.assertRaises(ValueError):
                self.h.validate_profile({"sex": v})
        with self.assertRaises(ValueError) as cm:
            self.h.validate_profile({"HELIO_PASSWORD": "x"})
        self.assertIn("unknown field", T(cm.exception.msg))

    def test_birth_date(self):
        lo, hi = self.h.PROFILE_AGE
        self.assertEqual(self.h.validate_profile({"birth_date": self.born(40)}), {"birth_date": self.born(40)})
        for v in (self.born(lo - 1), self.born(hi + 1), "2030-01-01", "1990-02-30", "01/01/1990", "1990-1-1", 1990):
            with self.assertRaises(ValueError, msg=str(v)):
                self.h.validate_profile({"birth_date": v})

    def test_empty_fields_are_removed(self):
        self.assertEqual(self.h.validate_profile({"height_cm": None, "sex": "", "hrmax": 190}), {"hrmax": 190})


class Effects(Base):
    def test_profile_overrides_gadgetbridge_and_recomputes(self):
        before = self.prof()
        self.assertEqual(before["src"]["height"], "Gadgetbridge")
        self.save(birth_date=self.born(50), sex="F", height_cm=160)
        p = self.prof()
        self.assertEqual((p["age"], p["sex"], p["height"]), (50, "F", 160))
        self.assertEqual((p["src"]["age"], p["src"]["sex"], p["src"]["height"]), ("app", "app", "app"))
        # Mifflin-St Jeor with the new values (woman: -161)
        self.assertEqual(p["bmr"], round(10 * p["weight"] + 6.25 * 160 - 5 * 50 - 161))
        # Tanaka 2001 with the new age
        self.assertEqual(p["hrmax_pred"], round(208 - 0.7 * 50))
        # stride estimated from the new height (in these tests HELIO_STRIDE_M is in the configuration)
        self.assertEqual(p["stride_m"], before["stride_m"])

    def test_manual_hrmax_rest_and_stride(self):
        self.save(hrmax=176, rest_hr=52, stride_m=0.68)
        p = self.prof()
        self.assertEqual((p["hrmax"], p["hrmax_src"]), (176, "set"))
        self.assertEqual((p["rest_hr"], p["stride_m"]), (52, 0.68))
        s = self.h.summary(DAY)
        self.assertEqual(s["zone_bounds"], [round(176 * x) for x in self.h.ZONE_PCT])
        self.assertAlmostEqual(s["distance_km"], round(s["steps"] * 0.68 / 1000, 2))

    def test_rest_hr_only_when_the_band_has_none(self):
        self.save(rest_hr=45)
        h = self.h
        orig = h.vo2max_for
        h.vo2max_for = lambda con, d, hrmax: (None, None, 0)
        try:
            p = self.prof()
            self.assertEqual(p["vo2max"], round(h.sci.uth_vo2max(p["hrmax"], 45), 1))
        finally:
            h.vo2max_for = orig

    def test_goals(self):
        self.save(steps_goal=6000, sleep_goal_min=450, active_goal=40, zone_goal_week=120)
        g = self.h.summary(DAY)["goals"]
        self.assertEqual((g["steps"], g["sleep"], g["active"], g["zone_week"]), (6000, 450, 40, 120))

    def test_reset_and_empty_profile_go_back(self):
        before = self.prof()
        self.save(height_cm=150, hrmax=170)
        self.assertEqual(self.h.save_profile({"reset": True}), {"ok": True})
        after = self.prof()
        self.assertEqual((after["height"], after["hrmax"], after["bmr"]), (before["height"], before["hrmax"], before["bmr"]))
        self.save(height_cm=150)
        self.save()  # empty profile: the file disappears
        self.assertFalse((self.state / "profile.json").exists())

    def test_weight_keeps_its_own_log(self):
        self.save(height_cm=165)
        self.h.save_weight(DAY, 70.5)
        p = self.prof()
        self.assertEqual((p["weight"], p["weight_src"], p["src"]["weight"]), (70.5, "log", "log"))

    def test_stored_only_in_the_state_folder(self):
        self.save(height_cm=165, sex="M")
        self.assertEqual(json.loads((self.state / "profile.json").read_text()), {"height_cm": 165.0, "sex": "M"})
        self.assertEqual(sorted(p.name for p in self.dir.rglob("*") if p.is_file()), ["gb.db", "profile.json"])

    def test_broken_or_tampered_file(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "profile.json").write_text(json.dumps({"height_cm": 999, "hrmax": 180, "HELIO_PASSWORD": "x"}))
        p = self.prof()
        self.assertEqual(p["hrmax"], 180)
        self.assertNotEqual(p["height"], 999)
        (self.state / "profile.json").write_text("{rotto")
        self.assertEqual(self.h.profile_load(), {})


class OverConfiguration(Base):
    env = {"HELIO_HEIGHT": "190", "HELIO_HRMAX": "200", "HELIO_ACTIVE_GOAL": "90"}

    def test_app_wins_over_the_configuration(self):
        p = self.prof()
        self.assertEqual((p["height"], p["hrmax"], p["active_goal"]), (190, 200, 90))
        self.assertEqual(p["src"]["height"], "configurazione")
        self.save(height_cm=170, hrmax=185, active_goal=30)
        p = self.prof()
        self.assertEqual((p["height"], p["hrmax"], p["active_goal"]), (170, 185, 30))
        self.assertEqual(self.h.profile_payload()["base"]["height_cm"], 190)  # hint: the previous value


class Api(Base):
    def setUp(self):
        super().setUp()
        self.srv = self.h.ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, body=None, headers=None):
        hd = {"Content-Type": "application/json", "X-Helio": "1", **(headers or {})}
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + "/api/profile", data=data, headers=hd, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_get_and_post(self):
        st, body = self.call("GET")
        self.assertEqual((st, body["saved"]), (200, {}))
        self.assertEqual(body["limits"]["height_cm"], {"min": 100, "max": 250, "unit": "u.cm"})
        self.assertEqual(self.call("POST", {"profile": {"height_cm": 172, "sex": "F"}}), (200, {"ok": True}))
        st, body = self.call("GET")
        self.assertEqual(body["saved"], {"height_cm": 172.0, "sex": "F"})
        self.assertEqual(body["effective"]["height"], 172.0)

    def test_rejected(self):
        st, body = self.call("POST", {"profile": {"height_cm": 20}})
        self.assertEqual(st, 400)
        self.assertIn("height outside the allowed range (100-250 cm)", T(body["error"]))
        self.assertEqual(self.call("POST", {"profile": {"height_cm": 172}}, {"X-Helio": "0"})[0], 403)
        self.assertEqual(self.call("POST", {"profile": {"x": "a" * 3000}})[0], 400)  # above the request limit
        self.assertFalse((self.state / "profile.json").exists())


class Transfer(Base):
    def test_profile_goes_with_export_and_import(self):
        import helio_transfer as ht
        self.save(height_cm=168, zone_goal_week=200)
        data = ht.export_bytes(self.state)
        target = self.dir / "other"
        ht.import_bytes(data, target)
        self.assertEqual(json.loads((target / "profile.json").read_text()), {"height_cm": 168.0, "zone_goal_week": 200})


if __name__ == "__main__":
    unittest.main()
