"""Tests of workouts recognised from heart rate, type suggestion, labels and calories by type.

Sources of the values: heart rate threshold below the ACSM moderate intensity (40% of reserve), METs of the
Compendium of Physical Activities 2024 (strength 3.5 / 5.0 / 7.5; swimming 5.8 / 9.8), Pandolf 1977 equation.
"""
import json
import math
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from test_helio import T, D0, DAY, build_db, hm, load_app

PLAN = {"1": "Rest", "2": "Strength training – day 1", "3": "Run", "4": "Strength training – day 2 (with load)",
        "5": "Swimming or skipping rope", "6": "Strength training – day 3", "7": "Active work"}


def minute_rows(spec, t0=D0 + 10 * 3600):
    """spec: list of (minutes, hr, steps, kind)."""
    rows, t = [], t0
    for n, hr, steps, kind in spec:
        for _ in range(n):
            rows.append({"t": t, "hr": hr, "hr_raw": hr, "steps": steps, "raw_steps": steps, "RAW_KIND": kind, "RAW_INTENSITY": 5})
            t += 60
    return rows


class Base(unittest.TestCase):
    def post(self, base, body, headers=None):
        h = {"Content-Type": "application/json", "X-Helio": "1"}
        h.update(headers or {})
        req = urllib.request.Request(base + "/api/label", data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        build_db(d / "gb.db")
        (d / "plan.json").write_text(json.dumps(PLAN))
        os.environ["HELIO_STATE"] = str(d / "state")
        cls.h = load_app(d / "gb.db", HELIO_PLAN=str(d / "plan.json"))
        cls.dir = d

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("HELIO_STATE", None)
        os.environ.pop("HELIO_PLAN", None)
        cls.tmp.cleanup()

    def feat(self, **kw):
        f = {"dur": 50, "steps": 0, "spm": 0, "cad": 0, "move": 0, "hrr": 0.4, "hrr_max": 0.6, "hr_sd": 5}
        f.update(kw)
        return f

    def plan(self, text):
        low = text.lower()
        types = [t for w, t in self.h.PLAN_WORDS if w in low]
        return {"text": text, "types": list(dict.fromkeys(types)), "day": None, "rest": False}


class Blocks(Base):
    def test_continuous_block_with_short_pauses(self):
        rows = minute_rows([(20, 130, 0, 80), (2, 80, 0, 80), (20, 125, 0, 80)])
        b = self.h.hr_blocks(rows, 55, 190, [])
        self.assertEqual(len(b), 1)
        self.assertEqual((b[0][1] - b[0][0]) // 60 + 1, 42)

    def test_long_pause_splits(self):
        rows = minute_rows([(15, 130, 0, 80), (5, 70, 0, 80), (15, 130, 0, 80)])
        self.assertEqual(len(self.h.hr_blocks(rows, 55, 190, [])), 2)

    def test_short_or_sleep_ignored(self):
        self.assertEqual(self.h.hr_blocks(minute_rows([(8, 140, 0, 80)]), 55, 190, []), [])
        self.assertEqual(self.h.hr_blocks(minute_rows([(30, 140, 0, 120)]), 55, 190, []), [])  # during sleep

    def test_below_threshold_ignored(self):
        # threshold 55 + 30% of 135 = 95.5
        self.assertEqual(self.h.hr_blocks(minute_rows([(40, 94, 100, 80)]), 55, 190, []), [])

    def test_gadgetbridge_session_extends_block(self):
        rows = minute_rows([(5, 80, 100, 80), (30, 120, 100, 80)])
        gb = [{"start": rows[0]["t"], "end": rows[-1]["t"], "source": "gadgetbridge"}]
        b = self.h.hr_blocks(rows, 55, 190, gb)
        self.assertEqual(b[0][0], rows[0]["t"])

    def test_synthetic_day(self):
        s = self.h.summary(DAY)
        tr = s["trainings"]
        self.assertEqual(len(tr), 2)
        self.assertEqual(tr[0]["start"], hm(9, 0))
        self.assertEqual(tr[0]["segments"], [hm(9, 0)])
        run = [w for w in s["workouts"] if w["kind"] == "running"][0]
        self.assertEqual((tr[1]["start"], tr[1]["suggestion"]["type"]), (run["start"], "run"))
        self.assertIsNone(T(tr[0]["label"]))


class Classifier(Base):
    def test_rules(self):
        p = self.plan("")
        self.assertEqual(self.h.rule_type(self.feat(cad=160, move=0.9, hrr=0.8), p)[0], "run")
        self.assertEqual(self.h.rule_type(self.feat(dur=318, cad=94, move=0.36, hrr=0.48, hr_sd=16.5), p)[0], "hike")
        self.assertEqual(self.h.rule_type(self.feat(cad=110, move=0.85, hrr=0.35, hr_sd=6), p)[0], "walk")
        self.assertEqual(self.h.rule_type(self.feat(dur=60, spm=61, cad=120, move=0.5, hrr=0.44, hr_sd=15.0), p)[0], "strength")
        self.assertEqual(self.h.rule_type(self.feat(spm=3, hrr=0.45), self.plan("Swimming"))[0], "swim")
        self.assertEqual(self.h.rule_type(self.feat(spm=3, hrr=0.45), p)[0], "strength")

    def test_plan_only_when_compatible(self):
        # strength workout done on a day planned for swimming or rope → it stays strength
        f = self.feat(dur=60, steps=3660, spm=61.0, cad=120, move=0.5, hrr=0.44, hrr_max=0.6, hr_sd=15.0)
        s = self.h.suggest(f, self.plan("Swimming or skipping rope"), {})
        self.assertEqual((s["type"], s["confidence"]), ("strength", "medium"))
        # few steps and swimming planned → swimming (plan written in Italian: both languages are read)
        s = self.h.suggest(self.feat(spm=4, hrr=0.4, hr_sd=12), self.plan("Nuoto o salto della corda"), {})  # lang: it
        self.assertEqual((s["type"], s["confidence"]), ("swim", "high"))

    def test_plan_parsing(self):
        import datetime
        for wd, (types, day) in {1: ([], None), 2: (["strength"], 1), 3: (["run"], None), 4: (["strength"], 2),
                                 5: (["swim", "rope"], None), 7: (["work"], None)}.items():
            d = DAY + datetime.timedelta(days=(wd - DAY.isoweekday()) % 7)
            p = self.h.plan_for(d)
            self.assertEqual((p["types"], p["day"]), (types, day), PLAN[str(wd)])
        monday = DAY + datetime.timedelta(days=(1 - DAY.isoweekday()) % 7)
        self.assertTrue(self.h.plan_for(monday)["rest"])

    def test_forza_day_from_plan(self):
        p = {"text": PLAN["4"], "types": ["strength"], "day": 2, "rest": False}
        s = self.h.suggest(self.feat(dur=50, spm=30, cad=90, move=0.3, hrr=0.5, hr_sd=14), p, {})
        self.assertEqual((s["type"], s["day"], T(s["name"]), s["confidence"]), ("strength", 2, "Strength training · day 2", "high"))

    def test_knn_learns_from_labels(self):
        walkish = dict(dur=55, spm=95, cad=112, move=0.8, hrr=0.4, hr_sd=8)
        labels = {str(i): {"type": "strength", "day": 3, "features": self.feat(**{**walkish, "dur": 55 + i})} for i in range(3)}
        labels["x"] = {"type": "run", "features": self.feat(dur=40, spm=150, cad=160, move=0.9, hrr=0.8, hr_sd=6)}
        s = self.h.suggest(self.feat(**walkish), self.plan(""), labels)
        self.assertEqual((s["type"], s["day"], s["confidence"]), ("strength", 3, "high"))
        self.assertIn("labelled", " ".join(T(s["reason"])))

    def test_knn_needs_examples_and_similarity(self):
        f = self.feat(cad=110, move=0.85, hrr=0.35, hr_sd=6)
        two = {str(i): {"type": "strength", "features": f} for i in range(2)}
        self.assertIsNone(self.h.knn_type(f, two))
        far = {str(i): {"type": "strength", "features": self.feat(dur=300 + i, spm=5, cad=0, move=0, hrr=0.9, hr_sd=30)} for i in range(3)}
        far["a"] = {"type": "run", "features": self.feat(dur=40, spm=150, cad=160, move=0.9, hrr=0.8, hr_sd=6)}
        # a workout unlike all the labelled ones is not forced into a category
        self.assertIsNone(self.h.knn_type(self.feat(dur=20, spm=90, cad=100, move=0.5, hrr=0.3, hr_sd=10), far))


class PlanObjects(Base):
    def test_object_plan_with_exercises(self):
        import datetime
        plan = json.loads((Path(__file__).resolve().parent / "plan-sample.json").read_text())
        (self.dir / "plan.json").write_text(json.dumps(plan))
        try:
            got = {}
            for i in range(7):
                d = DAY + datetime.timedelta(days=i)
                got[d.isoweekday()] = self.h.plan_for(d)
            self.assertTrue(got[1]["rest"])
            self.assertEqual((got[2]["types"], got[2]["day"]), (["strength"], 1))
            self.assertEqual(got[3]["types"], ["walk", "run"])
            self.assertEqual((got[4]["types"], got[4]["day"]), (["strength"], 2))
            self.assertEqual(got[5]["types"], ["swim", "other"])
            self.assertEqual((got[6]["types"], got[6]["day"]), (["strength"], 3))
            self.assertEqual(got[7]["types"], ["walk"])
            names = [x[0] for x in got[4]["exercises"]]
            self.assertIn("Lunges with 10 kg", names)
            self.assertFalse(any("step-up" in n.lower() for g in got.values() for n, _ in g["exercises"]))
            s = self.h.summary(DAY)
            self.assertEqual(s["plan_today"], T(s["plan"]["text"]))
            json.dumps(s["plan"])
        finally:
            (self.dir / "plan.json").write_text(json.dumps(PLAN))


class Calories(Base):
    def p(self):
        return {"weight": 74.0, "bmr": 1649, "stride_m": 0.72, "age": 33, "sex": "M", "hrmax": 185, "rest": 60, "vo2max": 44.1}

    def test_forza_compendium(self):
        part = minute_rows([(60, 120, 0, 80)])
        for hrr, met in ((0.3, 3.5), (0.45, 5.0), (0.6, 7.5)):
            kcal, method = self.h.typed_kcal("strength", part, self.feat(hrr=hrr), self.p(), 110)
            method = T(method)
            self.assertAlmostEqual(kcal, (met - 1) * 74 * 1.0, places=6)
            self.assertIn(f"{met:g} MET", method)

    def test_nuoto_compendium(self):
        part = minute_rows([(30, 120, 0, 80)])
        kcal, _ = self.h.typed_kcal("swim", part, self.feat(hrr=0.4), self.p(), 110)
        self.assertAlmostEqual(kcal, 4.8 * 74 * 0.5)

    def test_pandolf_equation(self):
        # 74 kg, no load, 1.25 m/s on the level: 1.5·74 + 74·1.5·1.25² = 284.4 W = 4.076 kcal/min
        steps = 1.25 * 60 / 0.72
        self.assertAlmostEqual(self.h.pandolf_kcal_min(steps, self.p(), 0), 284.4375 * 0.01433, places=4)
        more = self.h.pandolf_kcal_min(steps, self.p(), 15)
        self.assertGreater(more, self.h.pandolf_kcal_min(steps, self.p(), 0))

    def test_walk_with_load_uses_pandolf(self):
        part = minute_rows([(30, 100, 100, 80)])
        kcal, method = self.h.typed_kcal("walk", part, self.feat(), self.p(), 110, 10)
        method = T(method)
        self.assertIn("Pandolf", method)
        expected = 30 * max(0, self.h.pandolf_kcal_min(100, self.p(), 10) - 1649 / 1440)
        self.assertAlmostEqual(kcal, expected)


class RopeInsideStrength(Base):
    """Strength days 2 and 3: rope warm-up + sets + rope finisher in the same session."""

    def p(self):
        return {"weight": 74.0, "bmr": 1649, "stride_m": 0.72, "age": 33, "sex": "M", "hrmax": 185, "rest": 60, "vo2max": 44.1}

    def session(self):
        # typical session: 9 min rope, 18 min sets, 14 min rope, 10 min stretching
        return minute_rows([(9, 122, 135, 80), (18, 128, 25, 80), (14, 128, 128, 80), (10, 100, 3, 80)])

    def test_rope_minutes_found(self):
        part = self.session()
        self.assertEqual(len(self.h.rope_minutes(part)), 23)
        runs = self.h.rope_runs(part)
        self.assertEqual([round((b - a) / 60) for a, b in runs], [9, 14])

    def test_single_fast_minute_is_not_rope(self):
        # standing up to fetch the weights: an isolated minute with many steps is not rope
        part = minute_rows([(5, 120, 10, 80), (1, 120, 110, 80), (5, 120, 10, 80)])
        self.assertEqual(self.h.rope_minutes(part), set())

    def test_whole_session_is_still_strength(self):
        part = self.session()
        f = self.h.train_features(part, 60, 185)
        self.assertEqual(f["rope_min"], 23)
        for plan in ("Strength training – day 2", "Swimming or skipping rope"):
            self.assertEqual(self.h.suggest(f, self.plan(plan), {})["type"], "strength", plan)

    def test_rope_only_session_is_rope(self):
        # rope only: 25 minutes, almost all of them jumping
        part = minute_rows([(2, 110, 20, 80), (23, 150, 130, 80)])
        f = self.h.train_features(part, 60, 185)
        self.assertEqual(self.h.suggest(f, self.plan("Swimming or skipping rope"), {})["type"], "rope")

    def test_strength_kcal_split(self):
        part = self.session()
        f = self.h.train_features(part, 60, 185)
        kcal, method = self.h.typed_kcal("strength", part, f, self.p(), 110, 15, 60)
        method = T(method)
        rope = self.h.rope_minutes(part)
        strength = [r for r in part if r["t"] not in rope]
        hrr = (sum(r["hr"] for r in strength) / len(strength) - 60) / (185 - 60)
        met = 3.5 if hrr < 0.35 else 5.0 if hrr < 0.55 else 7.5
        expected = (met - 1) * 74 * len(strength) / 60 + sum(self.h.kcal_active_minute(r, self.p(), 110) for r in part if r["t"] in rope)
        self.assertAlmostEqual(kcal, expected)
        self.assertIn("rope", method)
        self.assertIn("23 min", method)
        # rope counts more than a minute of sets for the same time: the total exceeds the strength MET alone
        self.assertGreater(kcal, (met - 1) * 74 * len(part) / 60)


class LabelsApi(Base):
    def test_label_flow(self):
        srv = self.h.ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        lan = self.h.ThreadingHTTPServer(("127.0.0.1", 0), self.h.LanHandler)
        for s in (srv, lan):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            before = self.h.summary(DAY)
            walk = before["trainings"][0]
            body = {"start": walk["start"], "end": walk["end"], "type": "strength", "day": 2, "load_kg": 15}
            self.assertEqual(self.post(base, body, {"X-Helio": ""})[0], 403)        # no header
            self.assertEqual(self.post(base, {**body, "type": "volo"})[0], 400)       # unknown type
            self.assertEqual(self.post(base, {**body, "day": 7})[0], 400)
            self.assertEqual(self.post(base, {**body, "load_kg": 500})[0], 400)
            self.assertEqual(self.post(f"http://127.0.0.1:{lan.server_address[1]}", body)[0], 401)  # home network: password needed
            self.assertEqual(self.post(base, body), (200, {"ok": True}))

            f = self.dir / "state" / "labels.json"
            self.assertTrue(f.is_file())
            self.assertFalse(f.with_suffix(".tmp").exists())
            saved = json.loads(f.read_text())
            lb = next(iter(saved.values()))
            self.assertEqual((lb["type"], lb["day"], lb["load_kg"]), ("strength", 2, 15))
            self.assertIn("hr_sd", lb["features"])

            after = self.h.summary(DAY)
            t = after["trainings"][0]
            self.assertEqual(T(t["name"]), "Strength training · day 2")
            self.assertEqual(T(t["label"])["load_kg"], 15)
            self.assertIn("Compendium", T(t["kcal_method"]))
            delta = t["kcal"] - t["kcal_default"]
            self.assertEqual(after["kcal"]["active"], round(before["kcal"]["active"] + delta) or after["kcal"]["active"])
            self.assertEqual(after["kcal"]["total"], after["kcal"]["bmr"] + after["kcal"]["active"])

            self.assertEqual(self.post(base, {**body, "type": None}), (200, {"ok": True}))  # deletes
            self.assertIsNone(T(self.h.summary(DAY)["trainings"][0]["label"]))
        finally:
            for s in (srv, lan):
                s.shutdown()
                s.server_close()


class ManualActivities(Base):
    def test_manual_work_shift(self):
        srv = self.h.ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            before = self.h.summary(DAY)
            shift = {"start": hm(14, 0), "end": hm(20, 0), "type": "work", "manual": True}
            self.assertEqual(self.post(base, {**shift, "end": hm(14, 0) + 13 * 3600})[0], 400)      # more than 12 hours
            self.assertEqual(self.post(base, {**shift, "start": hm(14, 0) + 3 * 86400,
                                              "end": hm(15, 0) + 3 * 86400})[0], 400)            # in the future
            self.assertEqual(self.post(base, shift), (200, {"ok": True}))

            after = self.h.summary(DAY)
            tr = after["trainings"]
            work = [t for t in tr if t["source"] == "manual"]
            self.assertEqual(len(work), 1)
            w = work[0]
            self.assertEqual((T(w["name"]), w["start"], w["end"], w["duration_min"]), ("Active work", hm(14, 0), hm(20, 0), 360))
            self.assertIn("Compendium", T(w["kcal_method"]))
            # the 18:00 run falls inside the shift: it belongs to it instead of appearing twice
            self.assertFalse(any(t["source"] == "battito" and hm(14, 0) <= t["start"] < hm(20, 0) for t in tr))
            self.assertEqual(after["kcal"]["total"], after["kcal"]["bmr"] + after["kcal"]["active"])
            # standing base (Compendium 2024, 07041: 1.5 MET) plus the steps (Looney 2019); the run's minutes, with the
            # heart rate above the threshold, counted with the heart rate where that gives more
            self.assertIn("07041", T(w["kcal_method"]))
            from contextlib import closing
            import helio_science as sci
            with closing(self.h.connect()) as con:
                day = self.h.compute_day(con, DAY, self.h.profile(con))
            p = day["profile"]
            thr = p["rest"] + 0.4 * (p["hrmax"] - p["rest"])
            want = 0.0
            for r in day["rows"]:
                if hm(14, 0) <= r["t"] < hm(20, 0):
                    m = 0.5 * 74 / 60 + sci.walk_kcal(r["steps"], 0.72, 74)
                    if r["hr"] is not None and r["hr"] >= thr:
                        m = max(m, sci.hr_active_kcal(r["hr"], p["rest"], p["hrmax"], p["vo2max"], p["bmr"], 74))
                    want += m
            self.assertAlmostEqual(w["kcal"], round(want), delta=1)

            self.assertEqual(self.post(base, {**shift, "type": None}), (200, {"ok": True}))
            self.assertEqual([t["start"] for t in self.h.summary(DAY)["trainings"]], [t["start"] for t in before["trainings"]])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
