"""Tests of the session load: every strength exercise and the plank are done with the added load, running, swimming
and rope with body weight only. One weight per session: in the plan (15 kg by default), in the confirmed workout and in
the strength log, which stay aligned. Calories follow the Compendium 2024: METs are per kg of body weight, with
weights on the activity code changes (02054, 02052, 02040), the load is not added to the weight.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from test_features import END, PLAN, Base, ts
from test_helio import T
from test_trainings import minute_rows

APP_DIR = Path(__file__).resolve().parent.parent
TUE, THU = date(2026, 3, 17), date(2026, 3, 19)


class PlanLoad(Base):
    def test_sample_plan_has_10_kg_on_strength_days(self):
        plan = json.loads((Path(__file__).resolve().parent / "plan-sample.json").read_text())
        self.assertEqual({k: v.get("load_kg") for k, v in plan.items()},
                         {"1": None, "2": 10, "3": None, "4": 10, "5": None, "6": 10, "7": None})
        self.assertEqual(self.h.validate_plan(plan)["4"]["load_kg"], 10)

    def test_validation(self):
        base = {"text": "Strength", "types": ["strength"], "day": 1, "exercises": []}
        for v in (0, 12.5, 60):
            self.assertEqual(self.h.validate_plan({"2": {**base, "load_kg": v}})["2"]["load_kg"], v)
        for v in (-1, 61, "15", True, float("nan")):
            with self.assertRaises(ValueError, msg=str(v)):
                self.h.validate_plan({"2": {**base, "load_kg": v}})
        self.assertNotIn("load_kg", self.h.validate_plan({"2": base})["2"])                         # no load given
        self.assertNotIn("load_kg", self.h.validate_plan({"3": {"text": "Run", "types": ["run"], "load_kg": 10}})["3"])

    def test_plan_for_and_payload(self):
        self.h.save_plan({"plan": {**PLAN, "2": {**PLAN["2"], "load_kg": 12}}})
        self.assertEqual(self.h.plan_for(TUE)["load_kg"], 12)
        self.assertIsNone(self.h.plan_for(THU)["load_kg"])                 # in the test plan Thursday has none
        self.assertIsNone(self.h.plan_for(date(2026, 3, 18))["load_kg"])   # run: body weight only
        pl = self.h.plan_payload()["plan"]
        self.assertEqual(pl["2"]["load_kg"], 12)
        self.assertNotIn("load_kg", pl["3"])
        self.h.save_plan({"reset": True})

    def test_session_load_defaults(self):
        self.assertEqual(self.h.session_load({"load_kg": 20, "exercises": [["Lunges with 15 kg", ""]]}), 20)
        self.assertEqual(self.h.session_load({"load_kg": None, "exercises": [["Lunges with 15 kg", ""]]}), 15)
        self.assertEqual(self.h.session_load({"load_kg": None, "exercises": [["Rope 10 kg", ""], ["Plank", ""]]}), 0)  # rope excluded
        self.assertEqual(self.h.session_load(None), 0)
        self.assertEqual((self.h.strength_day_load(1), self.h.strength_day_load(2), self.h.strength_day_load(3)), (0, 15, 0))
        self.assertEqual(self.h.summary(END)["strength_day_loads"], {"1": 0, "2": 15, "3": 0})


class Labels(Base):
    def training(self, d):
        return next(t for t in self.h.summary(d)["trainings"] if (t.get("label") or t["suggestion"])["type"] == "strength")

    def test_suggestion_carries_the_plan_load(self):
        t = self.training(THU)                                             # Thursday 19 only suggested: strength day 2
        self.assertIsNone(T(t["label"]))
        self.assertEqual((t["suggestion"]["type"], t["suggestion"]["day"], t["suggestion"]["load_kg"]), ("strength", 2, 15))
        other = [x for x in self.h.summary(END)["trainings"]]
        self.assertTrue(all(x["suggestion"]["load_kg"] == 0 for x in other if x["suggestion"]["type"] != "strength"))

    def test_kcal_do_not_change_with_the_load_but_the_code_does(self):
        part = minute_rows([(40, 120, 0, 80)])
        p = {"weight": 74.0, "bmr": 1649, "stride_m": 0.71, "age": 33, "sex": "M", "hrmax": 185, "rest": 60, "vo2max": 44.1}
        f = {"dur": 40, "steps": 0, "spm": 0, "cad": 0, "move": 0, "hrr": 0.4, "hrr_max": 0.6, "hr_sd": 5}
        for hr, met, code in ((100, 3.5, "02054"), (120, 5.0, "02052"), (150, 7.5, "02040")):
            part = minute_rows([(40, hr, 0, 80)])
            free, m_free = self.h.typed_kcal("strength", part, f, p, 110, 0, 60)
            m_free = T(m_free)
            loaded, m_load = self.h.typed_kcal("strength", part, f, p, 110, 15, 60)
            m_load = T(m_load)
            self.assertAlmostEqual(free, loaded)                           # METs per kg of body weight
            self.assertAlmostEqual(loaded, (met - 1) * 74 * 40 / 60)
            self.assertIn(f"{met:g} MET", m_load)
            self.assertIn(f"code {code}", m_load)
            self.assertIn("added weight 15 kg", m_load)
            self.assertNotIn("code", m_free)

    def test_confirmed_training_shows_the_load_in_the_method(self):
        t = self.training(TUE)
        self.h.save_label(t["start"], t["end"], "strength", 1, 15)
        t = self.training(TUE)
        self.assertEqual(T(t["label"])["load_kg"], 15)
        self.assertIn("added weight 15 kg", T(t["kcal_method"]))
        self.assertIn("rope", T(t["kcal_method"]))                            # rope inside the session stays separate

    def test_strength_log_and_training_stay_in_sync(self):
        t = self.training(TUE)
        entries = [{"name": "Wide-grip pull-ups", "sets": [{"reps": 8}, {"reps": 6}]}, {"name": "Plank", "sets": [{"sec": 60}]}]
        self.assertEqual(self.h.save_strength(TUE, entries, 12.5), {"ok": True})
        rec = self.h.strength_load()[TUE.isoformat()]
        self.assertEqual(rec["load_kg"], 12.5)
        self.assertEqual([st["kg"] for e in rec["entries"] for st in e["sets"]], [12.5, 12.5, 12.5])
        self.assertEqual(T(self.training(TUE)["label"])["load_kg"], 12.5)    # the log updates the confirmed workout
        self.h.save_label(t["start"], t["end"], "strength", 1, 20)            # and the workout updates the log
        rec = self.h.strength_load()[TUE.isoformat()]
        self.assertEqual((rec["load_kg"], [st["kg"] for e in rec["entries"] for st in e["sets"]]), (20, [20, 20, 20]))
        # a session logged before session loads existed (kg set by set) is left alone
        self.h.save_strength(THU, [{"name": "Lunges with 15 kg", "sets": [{"reps": 10, "kg": 15}, {"reps": 10, "kg": 10}]}])
        u = self.training(THU)
        self.h.save_label(u["start"], u["end"], "strength", 2, 25)
        self.assertEqual([st["kg"] for st in self.h.strength_load()[THU.isoformat()]["entries"][0]["sets"]], [15, 10])
        # switching to swimming the load does not follow
        self.h.save_label(t["start"], t["end"], "swim", None, 20)
        self.assertEqual(self.h.strength_load()[TUE.isoformat()]["load_kg"], 20)

    def test_invalid_session_load(self):
        for v in (-1, 61, "12", True, float("nan")):
            with self.assertRaises(ValueError, msg=str(v)):
                self.h.save_strength(TUE, [{"name": "Plank", "sets": [{"sec": 30}]}], v)
        self.assertFalse((self.state / "strength.json").exists())


class StrengthDay(Base):
    def test_load_sources(self):
        sd = self.h.strength_day(THU)                                      # no log, suggestion not confirmed
        self.assertEqual((sd["load_kg"], sd["load_src"]), (15, "plan"))   # from the name "Lunges with 15 kg"
        self.assertEqual(self.h.strength_day(TUE)["load_kg"], 0)          # day 1 without load in the test plan
        lb = json.loads((self.state / "labels.json").read_text())
        lb[str(ts(TUE, 18))]["load_kg"] = 18
        (self.state / "labels.json").write_text(json.dumps(lb))
        self.h._daycache.clear()
        self.assertEqual((self.h.strength_day(TUE)["load_kg"], self.h.strength_day(TUE)["load_src"]), (18, "allenamento"))
        self.h.save_strength(TUE, [{"name": "Plank", "sets": [{"sec": 60}]}], 10)
        self.assertEqual((self.h.strength_day(TUE)["load_kg"], self.h.strength_day(TUE)["load_src"]), (10, "log"))
        self.h.save_strength(THU, [{"name": "Squat", "sets": [{"reps": 20, "kg": 8}]}])   # old format: kg of the sets
        self.assertEqual((self.h.strength_day(THU)["load_kg"], self.h.strength_day(THU)["load_src"]), (8, "log"))

    def test_volume_and_hold(self):
        self.h.save_strength(TUE, [{"name": "Plank", "sets": [{"sec": 60}, {"sec": 45}]},
                                   {"name": "Parallel bar dips", "sets": [{"reps": 8}, {"reps": 7}]}], 15)
        sd = self.h.strength_day(TUE)
        prog = {T(p["name"]): p["sessions"][-1] for p in sd["progress"]}
        self.assertEqual((prog["Plank"]["hold"], prog["Plank"]["best_kg"]), (105 * 15, 15))
        self.assertEqual(prog["Parallel bar dips"]["volume"], 15 * 15)
        self.assertEqual(sd["week"], {"days": 1, "sets": 4, "volume": 225.0, "hold": 1575.0})

    def test_export_uses_the_session_load(self):
        self.h.save_strength(TUE, [{"name": "Plank", "sets": [{"sec": 60}]}], 15)
        rows = self.h.export_csv("strength", TUE, TUE, "std").strip().splitlines()
        self.assertEqual(rows[1], "2026-03-17,Plank,1,,15,60,")


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
