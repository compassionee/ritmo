"""Cumulative balance and body composition estimate.

Part A, the cumulative balance: same day criteria as the weight check (diary complete and not empty, band on the wrist
for at least 20 hours, day over), days left out never estimated, linear sum of the extremes, calendar and rolling
periods, coverage, fat equivalent as an upper bound (Hall 2008).

Part B, the composition: Lambert W, Forbes's curve extended by Hall 2007, Gallagher 2000 equation 5 (checked against
Table 4 of the paper), ranges over every combination, energy check, protein and strength signals.
"""
import json
import math
import random
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from test_helio import T, DAY
from test_nutrition import StoreBase, hist_day

import helio_science as sci


class LambertW(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(sci.lambert_w(0), 0.0)
        self.assertAlmostEqual(sci.lambert_w(1), 0.5671432904097838, places=15)   # omega constant
        self.assertAlmostEqual(sci.lambert_w(math.e), 1.0, places=15)
        self.assertAlmostEqual(sci.lambert_w(2 * math.exp(2)), 2.0, places=14)
        self.assertAlmostEqual(sci.lambert_w(10), 1.7455280027406994, places=14)

    def test_inverse_over_many_magnitudes(self):
        for e in range(-12, 13):
            for m in (1.0, 2.5, 7.3):
                x = m * 10 ** e
                w = sci.lambert_w(x)
                self.assertAlmostEqual(w * math.exp(w) / x, 1.0, places=12, msg=x)

    def test_small_and_huge(self):
        self.assertAlmostEqual(sci.lambert_w(1e-12), 1e-12, places=22)         # W(x) ≈ x near zero
        w = sci.lambert_w(1e300)
        self.assertAlmostEqual(w + math.log(w), math.log(1e300), places=10)
        w = sci.lambert_w_exp(5000)                                              # e^5000 does not fit in a float
        self.assertAlmostEqual(w + math.log(w), 5000, places=9)
        for lx in (-30, -1, 0, 0.5, 1, 2, 50):
            self.assertAlmostEqual(sci.lambert_w_exp(lx), sci.lambert_w(math.exp(lx)), places=12, msg=lx)

    def test_monotonic(self):
        xs = [i / 10 for i in range(0, 500)]
        ws = [sci.lambert_w(x) for x in xs]
        self.assertTrue(all(b > a for a, b in zip(ws, ws[1:])))

    def test_invalid_input(self):
        for bad in (-0.1, float("nan"), "1", None, True):
            with self.assertRaises(ValueError, msg=bad):
                sci.lambert_w(bad)


class ForbesHall(unittest.TestCase):
    def test_small_change_tends_to_forbes(self):
        for fm in (8, 15, 25, 40, 60):
            for d in (-1e-4, 1e-4):
                d_fm, d_ffm = sci.forbes_change(fm, d)
                self.assertAlmostEqual(d_ffm / d, 10.4 / (10.4 + fm), places=5, msg=(fm, d))
            self.assertAlmostEqual(sci.forbes_lean_share(fm), 10.4 / (10.4 + fm))

    def test_parts_add_up(self):
        for fm in (6, 20, 45):
            for d in (-30, -5, -0.3, 0, 0.3, 5, 30):
                d_fm, d_ffm = sci.forbes_change(fm, d)
                self.assertAlmostEqual(d_fm + d_ffm, d, places=12)
                self.assertGreater(fm + d_fm, 0)                                   # fat never below zero

    def test_change_stays_on_forbes_curve(self):
        # FFM = 10.4·ln(FM) + c: the lean change is exactly 10.4·ln(FMf/FMi)
        for fm, d in ((20, -10), (35, 8), (12, -4)):
            d_fm, d_ffm = sci.forbes_change(fm, d)
            self.assertAlmostEqual(d_ffm, 10.4 * math.log((fm + d_fm) / fm), places=10)

    def test_matches_hall_2007_equation_8(self):
        # ΔFFM/ΔBW = 1 + FMi/ΔBW − (10.4/ΔBW)·W{(1/10.4)·exp(ΔBW/10.4)·FMi·exp(FMi/10.4)}
        for fm, d in ((30, -20), (50, -45), (15, 6), (25, -2)):
            arg = (1 / 10.4) * math.exp(d / 10.4) * fm * math.exp(fm / 10.4)
            ratio = 1 + fm / d - (10.4 / d) * sci.lambert_w(arg)
            self.assertAlmostEqual(sci.forbes_change(fm, d)[1] / d, ratio, places=12)

    def test_loss_and_gain_are_consistent(self):
        # going down and coming back along the curve returns to the start
        for fm in (10, 25, 50):
            d_fm, d_ffm = sci.forbes_change(fm, -8)
            back_fm, back_ffm = sci.forbes_change(fm + d_fm, 8)
            self.assertAlmostEqual(d_fm + back_fm, 0, places=10)
            self.assertAlmostEqual(d_ffm + back_ffm, 0, places=10)
        # a loss has a larger lean share than a gain of the same size (the curve is steeper at lower fat)
        self.assertGreater(sci.forbes_change(20, -10)[1] / -10, sci.forbes_change(20, 10)[1] / 10)

    def test_more_fat_means_a_smaller_lean_share(self):
        shares = [sci.forbes_change(fm, -5)[1] / -5 for fm in (8, 15, 25, 40, 60)]
        self.assertTrue(all(b < a for a, b in zip(shares, shares[1:])))

    def test_large_loss_costs_more_lean_than_forbes_linear(self):
        # Hall 2007: for large losses Forbes's original (linear) equation underestimates the lean mass lost
        # (bariatric example: 6.7 kg predicted by Forbes, 9.9 kg by the new equation, 11.3 kg measured)
        for fm, d in ((60, -45), (45, -30)):
            linear = d * sci.forbes_lean_share(fm)
            self.assertLess(sci.forbes_change(fm, d)[1], linear)                # more negative: more lean lost
        self.assertAlmostEqual(sci.forbes_change(20, -0.5)[1], -0.5 * sci.forbes_lean_share(20), places=2)  # small: close

    def test_invalid_fat(self):
        with self.assertRaises(ValueError):
            sci.forbes_change(0, -1)


class Gallagher(unittest.TestCase):
    # Gallagher 2000, Table 4: equation 5 at 30, 50 and 70 years, BMI 18.5, 25 and 30 (white and African American)
    TABLE4 = {"F": {18.5: (21, 23, 24), 25: (33, 34, 36), 30: (39, 40, 42)},
              "M": {18.5: (8, 11, 13), 25: (20, 22, 25), 30: (25, 28, 30)}}

    def test_values_of_the_paper(self):
        # the table was computed with unrounded coefficients: within 0.6 points of the printed equation
        for sex, rows in self.TABLE4.items():
            for bmi, vals in rows.items():
                for age, v in zip((30, 50, 70), vals):
                    self.assertLessEqual(abs(sci.gallagher_bf(bmi, age, sex) - v), 0.6, (sex, bmi, age))

    def test_equation_terms(self):
        self.assertAlmostEqual(sci.gallagher_bf(25, 40, "F"), 64.5 - 848 / 25 + 0.079 * 40)
        self.assertAlmostEqual(sci.gallagher_bf(25, 40, "M"), 64.5 - 848 / 25 + 0.079 * 40 - 16.4 + 0.05 * 40 + 39.0 / 25)
        self.assertEqual(sci.GALLAGHER_SEE, 4.98)
        self.assertGreater(sci.gallagher_bf(30, 40, "F"), sci.gallagher_bf(25, 40, "F"))   # more BMI, more fat
        self.assertGreater(sci.gallagher_bf(25, 60, "M"), sci.gallagher_bf(25, 30, "M"))   # older, more fat


class CompositionRanges(unittest.TestCase):
    def test_central_value_always_inside_the_range(self):
        rnd = random.Random(7)
        for _ in range(400):
            fm = rnd.uniform(5, 60)
            sd = rnd.uniform(0.5, 10)
            d = rnd.uniform(-15, 15)
            half = rnd.uniform(0, 4)
            c = sci.composition_change(fm, max(1, fm - sd), fm + sd, d, d - half, d + half)
            for k in ("fat", "lean", "energy", "lean_share"):
                lo, hi = c[f"{k}_range"]
                self.assertLessEqual(lo - 1e-9, c[k], k)
                self.assertLessEqual(c[k], hi + 1e-9, k)

    def test_energy_uses_hall_densities(self):
        c = sci.composition_change(20, 15, 25, -3, -4, -2)
        self.assertAlmostEqual(c["energy"], c["fat"] * sci.FAT_KCAL_PER_KG + c["lean"] * sci.LEAN_KCAL_PER_KG)
        self.assertAlmostEqual(c["fat"] + c["lean"], -3)


FOOD = {"kcal": 200, "protein": 20, "carbs": 20, "fat": 4.4, "fiber": 2}
LO, HI = sci.food_range(FOOD)
STRENGTH = {"label": {"type": "strength"}, "duration_min": 45, "start": 0, "end": 2700, "name": "x"}


class BalanceBase(StoreBase):
    P = {"weight": 80.0, "height": 175, "age": 40, "sex": "M", "missing": []}

    def setUp(self):
        super().setUp()
        self.fid = self.food("Food", FOOD)

    def log(self, d, g=1000, complete=True):
        self.h.diary_op({"date": d.isoformat(), "op": "add", "meal": "lunch", "food": self.fid, "g": g})
        if complete:
            self.h.diary_op({"date": d.isoformat(), "op": "complete", "complete": True})

    def analyse(self, period, d=DAY, hist=None, since=None, bf=None, p=None, n=60, goal="lose_fat"):
        hist = hist if hist is not None else [hist_day(d - timedelta(days=i)) for i in range(n - 1, -1, -1)]
        rng = self.h.balance_period(period, d, since)
        diary = self.h.diary_days(*rng) if rng else {}
        return self.h.balance_analysis(d, period, hist, diary, self.h.weights_load(), p or self.P, goal, DAY, since, bf)

    # one day: 2000 kcal eaten, 2500 spent (2400-2600): -500 with the label tolerances
    B = (2000 - 2500, round(LO["kcal"], 3) * 10 - 2600, round(HI["kcal"], 3) * 10 - 2400)


class Balance(BalanceBase):
    def test_periods(self):
        d = DAY                                                                  # Tuesday 10 March 2026
        self.assertEqual(self.h.balance_period("week", d), (date(2026, 3, 9), d))
        self.assertEqual(self.h.balance_period("prev_week", d), (date(2026, 3, 2), date(2026, 3, 8)))
        self.assertEqual(self.h.balance_period("28d", d), (d - timedelta(days=27), d))
        self.assertEqual(self.h.balance_period("month", d), (date(2026, 3, 1), d))
        self.assertEqual(self.h.balance_period("since", d, date(2026, 2, 1)), (date(2026, 2, 1), d))
        self.assertIsNone(self.h.balance_period("since", d))
        self.assertIsNone(self.h.balance_period("since", d, d + timedelta(days=1)))
        self.assertEqual(self.h.balance_period("since", d, date(2020, 1, 1))[0], d - timedelta(days=730))
        sunday = date(2026, 3, 15)
        self.assertEqual(self.h.balance_period("week", sunday), (date(2026, 3, 9), sunday))       # Monday-Sunday
        self.assertEqual(self.h.balance_period("prev_week", date(2026, 3, 9)), (date(2026, 3, 2), date(2026, 3, 8)))

    def test_inclusion_and_exclusion(self):
        for i in range(1, 8):
            self.log(DAY - timedelta(days=i))
        self.log(DAY)                                                            # today: never in the balance
        reopened = DAY - timedelta(days=7)                                       # diary no longer marked complete
        self.h.diary_op({"date": reopened.isoformat(), "op": "complete", "complete": False})
        hist = [hist_day(DAY - timedelta(days=i)) for i in range(59, -1, -1)]
        hist[-3] = hist_day(DAY - timedelta(days=2), worn=1000)                  # band worn less than 20 hours
        r = self.analyse("prev_week", hist=hist)                                  # 2-8 March: 8 to 2 days before DAY
        self.assertEqual((r["days"], r["included"]), (7, 4))
        self.assertEqual(r["excluded"], {"diary": 2, "band": 1})                 # 2 March not logged, 3 March reopened
        r = self.analyse("week", hist=hist)                                       # Monday 9 March: today is excluded
        self.assertEqual((r["days"], r["included"], r["today_in_period"]), (1, 1, True))
        r = self.analyse("28d", hist=hist)
        self.assertEqual(r["excluded"]["band"], 1)
        self.assertEqual(r["included"], 5)                                       # 7 logged, 1 reopened, 1 band
        self.assertEqual(r["days"], 27)
        self.assertEqual(r["series"][-1][0], (DAY - timedelta(days=1)).isoformat())   # no point for today
        self.assertNotIn(DAY.isoformat(), [s[0] for s in r["series"]])

    def test_sums_and_ranges(self):
        for i in range(1, 6):
            self.log(DAY - timedelta(days=i))
        r = self.analyse("28d")
        self.assertEqual(r["included"], 5)
        self.assertEqual(r["total"], round(5 * self.B[0]))
        self.assertEqual(r["range"], [round(5 * self.B[1]), round(5 * self.B[2])])   # linear sum of the extremes
        self.assertEqual(r["mean"], round(self.B[0]))
        self.assertEqual(r["sign"], "deficit")
        self.assertFalse(r["sign_sure"])                                           # the range crosses zero
        s = r["series"]
        self.assertEqual(s[-1][1:4], [r["total"], r["range"][0], r["range"][1]])
        for a, b in zip(s, s[1:]):                                                 # flat on excluded days: no extrapolation
            if not b[4]:
                self.assertEqual(a[1:4], b[1:4])
        fat = r["fat_max"]
        self.assertEqual(fat["dir"], "lost")
        self.assertAlmostEqual(fat["kg"], -5 * self.B[0] / sci.FAT_KCAL_PER_KG, places=2)
        self.assertEqual(fat["range"][0], 0)                                       # the best case is no deficit at all
        self.assertAlmostEqual(fat["range"][1], -5 * self.B[1] / sci.FAT_KCAL_PER_KG, places=2)
        json.dumps(r)

    def test_no_extrapolation_and_partial_total(self):
        for i in (1, 2, 3):
            self.log(DAY - timedelta(days=i))
        r = self.analyse("28d")
        self.assertEqual(r["total"], round(3 * self.B[0]))                         # 3 days, not 27 × the average
        self.assertTrue(r["partial"])
        self.assertAlmostEqual(r["coverage"], 3 / 27, places=3)
        for i in range(4, 22):
            self.log(DAY - timedelta(days=i))
        r = self.analyse("28d")
        self.assertAlmostEqual(r["coverage"], 21 / 27, places=3)
        self.assertFalse(r["partial"])                                              # 78% ≥ 70%
        self.assertEqual(r["coverage_min"], 0.7)

    def test_surplus(self):
        for i in range(1, 4):
            self.log(DAY - timedelta(days=i), g=1600)                               # 3200 kcal
        r = self.analyse("28d")
        self.assertEqual((r["sign"], r["total"]), ("surplus", 3 * 700))
        self.assertEqual(r["fat_max"]["dir"], "gained")
        self.assertAlmostEqual(r["fat_max"]["kg"], 2100 / sci.FAT_KCAL_PER_KG, places=2)
        self.assertEqual(r["fat_max"]["range"][0], max(0, round(r["range"][0] / sci.FAT_KCAL_PER_KG, 2)))

    def test_month_and_since(self):
        for i in range(1, 15):
            self.log(DAY - timedelta(days=i))
        r = self.analyse("month")                                                  # 1-9 March
        self.assertEqual((r["start"], r["days"], r["included"]), ("2026-03-01", 9, 9))
        r = self.analyse("since", since=date(2026, 3, 5))
        self.assertEqual((r["start"], r["days"], r["included"]), ("2026-03-05", 5, 5))
        r = self.analyse("since")
        self.assertEqual((r["status"], r["composition"]), ("need_date", None))

    def test_empty_and_collecting(self):
        r = self.analyse("week", d=DAY, hist=[hist_day(DAY)])
        self.assertIn(r["status"], ("collecting", "empty"))
        r = self.analyse("28d")
        self.assertEqual(r["status"], "collecting")
        self.assertNotIn("total", r)


class Composition(BalanceBase):
    def weigh(self, kgs, d=DAY, step=7):
        for i, kg in enumerate(kgs):
            self.h.save_weight(d - timedelta(days=step * (len(kgs) - 1 - i)), kg)

    def log_all(self, days=27):
        for i in range(1, days + 1):
            self.log(DAY - timedelta(days=i))

    def test_requirements(self):
        self.log_all()
        c = self.analyse("28d")["composition"]
        self.assertEqual(c["status"], "collecting")
        self.assertIn("4 weigh-ins", T(c["text"]))
        self.weigh([80, 79.8, 79.6])                                               # 3 weigh-ins over 14 days
        c = self.analyse("28d")["composition"]
        self.assertEqual((c["status"], c["weighins"], c["span"]), ("collecting", 3, 14))
        self.weigh([80, 79.8, 79.6, 79.4])                                         # 4 over 21 days
        c = self.analyse("28d")["composition"]
        self.assertEqual(c["status"], "ok")
        c = self.analyse("week")["composition"]                                    # a week is too short
        self.assertEqual(c["status"], "collecting")
        self.assertIn("too short", T(c["text"]))
        c = self.analyse("28d", p={**self.P, "missing": ["sex", "height"]})["composition"]
        self.assertEqual(c["status"], "collecting")
        self.assertEqual(c["profile_missing"], ["sex", "height"])
        self.assertIn("sex", T(c["text"]))

    def test_estimate_from_bmi(self):
        self.log_all()
        self.weigh([82, 81.4, 80.8, 80.2, 79.6], step=6)                          # -0.1 kg a day, perfect line
        c = self.analyse("28d")["composition"]
        self.assertEqual(c["fm_src"]["kind"], "gallagher")
        bmi = 82 / 1.75 ** 2
        self.assertEqual(c["fm_src"]["bmi"], round(bmi, 1))
        pct = sci.gallagher_bf(bmi, 40, "M")
        self.assertAlmostEqual(c["fm0"], 82 * pct / 100, places=2)
        self.assertEqual(c["fm0_range"], [round(82 * (pct - 4.98) / 100, 2), round(82 * (pct + 4.98) / 100, 2)])
        self.assertAlmostEqual(c["weight"], -2.4, places=2)                       # slope × 24 days
        d_fm, d_ffm = sci.forbes_change(82 * pct / 100, -2.4)
        self.assertAlmostEqual(c["fat"], d_fm, places=2)
        self.assertAlmostEqual(c["lean"], d_ffm, places=2)
        self.assertAlmostEqual(c["fat"] + c["lean"], c["weight"], places=1)
        for k in ("fat", "lean", "weight", "lean_share"):
            lo, hi = c[f"{k}_range"]
            self.assertLessEqual(lo, c[k])
            self.assertLessEqual(c[k], hi)
        self.assertFalse(c["stable"])
        self.assertIn("gallagher", [s["key"] for s in c["sources"]])

    def test_measurement_replaces_the_estimate(self):
        self.log_all()
        self.weigh([82, 81.4, 80.8, 80.2, 79.6], step=6)
        bf = {"bf_pct": 30.0, "bf_date": (DAY - timedelta(days=40)).isoformat(), "bf_method": "dexa"}
        c = self.analyse("28d", bf=bf)["composition"]
        self.assertEqual((c["fm_src"]["kind"], c["fm_src"]["method"], c["fm_src"]["sd"]), ("measured", "dexa", 3.2))
        self.assertAlmostEqual(c["fm0"], 82 * 0.30, places=2)
        self.assertEqual(c["fm0_range"], [round(82 * 0.268, 2), round(82 * 0.332, 2)])
        self.assertFalse(c["fm_src"]["old"])
        c_bia = self.analyse("28d", bf={**bf, "bf_method": "bia"})["composition"]
        self.assertEqual(c_bia["fm_src"]["sd"], 5.1)
        self.assertIn("oliver_bia", [s["key"] for s in c_bia["sources"]])
        self.assertGreater(c_bia["lean_range"][1] - c_bia["lean_range"][0], c["lean_range"][1] - c["lean_range"][0])
        # more fat at the start: a smaller share of lean mass
        c_hi = self.analyse("28d", bf={**bf, "bf_pct": 40.0})["composition"]
        self.assertLess(c_hi["lean_share"], c["lean_share"])
        # a measurement after the period is not used; an old one is flagged
        late = self.analyse("28d", bf={**bf, "bf_date": (DAY + timedelta(days=1)).isoformat()})["composition"]
        self.assertEqual(late["fm_src"]["kind"], "gallagher")
        old = self.analyse("28d", bf={**bf, "bf_date": (DAY - timedelta(days=300)).isoformat()})["composition"]
        self.assertTrue(old["fm_src"]["old"])
        partial = self.analyse("28d", bf={"bf_pct": 30.0})["composition"]
        self.assertEqual(partial["fm_src"]["kind"], "gallagher")

    def test_outside_the_equation_needs_a_measurement(self):
        self.log_all()
        self.weigh([115, 114.4, 113.8, 113.2, 112.6], step=6)                     # BMI 37.6
        c = self.analyse("28d")["composition"]
        self.assertEqual(c["status"], "need_measure")
        self.assertIn("body fat measurement", T(c["text"]))
        bf = {"bf_pct": 38.0, "bf_date": (DAY - timedelta(days=30)).isoformat(), "bf_method": "bia"}
        self.assertEqual(self.analyse("28d", bf=bf)["composition"]["status"], "ok")
        young = self.analyse("28d", p={**self.P, "age": 18})["composition"]
        self.assertEqual(young["status"], "need_measure")

    def test_stable_weight(self):
        self.log_all()
        self.weigh([80, 80.3, 79.8, 80.2, 79.9], step=6)
        c = self.analyse("28d")["composition"]
        self.assertTrue(c["stable"])
        self.assertLessEqual(c["weight_range"][0], 0)
        self.assertGreaterEqual(c["weight_range"][1], 0)

    def test_energy_check(self):
        self.log_all()                                                              # logged: -500 (-1086 / +86) a day
        self.weigh([80, 79.6, 79.2, 78.8, 78.4], step=6)                          # -0.067 kg a day
        e = self.analyse("28d")["composition"]["energy"]
        self.assertEqual(e["status"], "coherent")
        self.assertEqual(e["gap"], 0)
        # the logged balance is over the weigh-ins' span, never today: the dates are in the text
        self.assertEqual((e["from"], e["to"]), ((DAY - timedelta(days=24)).isoformat(), (DAY - timedelta(days=1)).isoformat()))
        self.assertEqual((e["text"]["from"], e["text"]["to"]), (e["from"], e["to"]))
        self.assertIn("Logged balance from", T(e["text"]))
        self.assertIn("−500", T(e["text"]))                                      # typographic minus
        self.assertNotRegex(T(e["text"]), r"(?<![\w])-\d")
        self.assertEqual(e["logged"], round(self.B[0]))
        self.assertIn("Compatible", T(e["text"]))
        self.weigh([84, 82.2, 80.4, 78.6, 76.8], step=6)                          # -0.3 kg a day: faster
        e = self.analyse("28d")["composition"]["energy"]
        self.assertEqual(e["status"], "faster")
        self.assertGreater(e["gap"], 0)
        self.assertLessEqual(abs(e["gap"] - (e["logged_range"][0] - e["body_range"][1])), 1)   # rounded values
        self.assertIn("more energy", T(e["text"]))
        self.weigh([78, 78.6, 79.2, 79.8, 80.4], step=6)                          # +0.1 kg a day: slower
        e = self.analyse("28d")["composition"]["energy"]
        self.assertEqual(e["status"], "slower")
        self.assertLessEqual(abs(e["gap"] - (e["body_range"][0] - e["logged_range"][1])), 1)
        self.assertIn("Burrows", T(e["text"]))

    def test_energy_check_needs_coverage(self):
        for i in range(1, 10):
            self.log(DAY - timedelta(days=i))
        self.weigh([80, 79.6, 79.2, 78.8, 78.4], step=6)
        c = self.analyse("28d")["composition"]
        self.assertEqual(c["status"], "ok")
        self.assertEqual(c["energy"]["status"], "collecting")
        self.assertIn("70%", T(c["energy"]["text"]))

    def test_signals(self):
        self.log_all()                                                              # 200 g protein a day = 2.5 g/kg
        self.weigh([80, 79.6, 79.2, 78.8, 78.4], step=6)
        hist = [hist_day(DAY - timedelta(days=i)) for i in range(59, -1, -1)]
        s = self.analyse("28d", hist=hist)["composition"]["signals"]
        self.assertEqual(s["protein_gkg"], 2.5)
        self.assertEqual((s["protein_ref"], s["strength_ref"], s["sessions"]), (1.9, 2, 0))
        self.assertEqual(s["level"], "mixed")                                      # protein ok, no strength
        self.assertEqual((s["you"], s["protein_ok"], s["strength_ok"], s["deficit_ok"]), ("protein_only", True, False, True))
        self.assertIn("little strength", T(s["judgement"]))
        self.assertIn("does not take protein", T(s["text"]))
        self.assertIn("the effect on lean mass is uncertain", T(s["text"]))
        hist = [hist_day(DAY - timedelta(days=i), trainings=[STRENGTH] if i % 3 == 0 else ())
                for i in range(59, -1, -1)]
        s = self.analyse("28d", hist=hist)["composition"]["signals"]
        self.assertEqual(s["sessions"], 10)
        self.assertAlmostEqual(s["strength_week"], 2.5)
        self.assertEqual((s["level"], s["you"], s["strength_ok"]), ("better", "both_good", True))
        self.assertEqual(T(s["judgement"]), "In your case lean mass loss is probably smaller than the estimate.")
        self.assertIn("Wycherley", T(s["text"]))
        self.assertEqual(s["deficit"], -round(self.B[0]))
        self.assertEqual({x["key"] for x in s["sources"]} >= {"refalo", "wycherley", "sardeli", "who", "murphy_koehler"}, True)
        s = self.analyse("28d", goal="maintain")["composition"]["signals"]
        self.assertEqual(s["protein_ref"], 1.6)

    def test_signals_low_protein_and_big_deficit(self):
        low = self.food("Low", {"kcal": 146, "protein": 2, "carbs": 30, "fat": 1.8, "fiber": 1})
        for i in range(1, 28):
            self.h.diary_op({"date": (DAY - timedelta(days=i)).isoformat(), "op": "add", "meal": "lunch", "food": low, "g": 1000})
            self.h.diary_op({"date": (DAY - timedelta(days=i)).isoformat(), "op": "complete", "complete": True})
        self.weigh([80, 79.4, 78.8, 78.2, 77.6], step=6)
        s = self.analyse("28d")["composition"]["signals"]
        self.assertEqual((s["level"], s["you"], s["protein_ok"], s["deficit_ok"]), ("worse", "both_low", False, False))
        self.assertEqual(s["deficit"], 1040)
        # above 500 kcal of deficit the judgement says the benefit of strength training shrinks
        self.assertEqual(T(s["judgement"]), "In your case lean mass loss is probably larger than the estimate; with a deficit "
                                            "above 500 kcal a day the benefit of strength training shrinks.")
        self.assertIn("probably larger", T(s["text"]))
        self.assertIn("above 500", T(s["text"]))

    def test_gain(self):
        self.log_all()
        self.weigh([78, 78.6, 79.2, 79.8, 80.4], step=6)
        c = self.analyse("28d")["composition"]
        self.assertGreater(c["fat"], 0)
        self.assertGreater(c["lean"], 0)
        self.assertEqual((c["signals"]["level"], c["signals"]["you"]), ("gain", "gain"))


class Texts(unittest.TestCase):
    """Lines in view are short (one judgement, about 90 characters at most) and carry no citations."""
    CAT = {lang: json.loads((Path(__file__).resolve().parent.parent / "static" / "i18n" / f"{lang}.json").read_text())
           for lang in ("it", "en")}

    def test_judgement_lines_are_short_and_without_citations(self):
        for lang, cat in self.CAT.items():
            keys = [k for k in cat if k.startswith(("bc.you.", "comp.short.", "comp.chip.", "cum.p.", "cum.fat_line",
                                                    "cum.days", "comp.share", "bc.judge"))]
            self.assertGreater(len(keys), 15)
            for k in keys:
                for text in (cat[k].values() if isinstance(cat[k], dict) else [cat[k]]):
                    self.assertLessEqual(len(text), 90, (lang, k))
                    self.assertNotRegex(text, r"\b(19|20)\d\d\b", (lang, k))          # no "Author 2012" in view


class Settings(StoreBase):
    def test_since_date_is_saved_and_exported(self):
        self.assertIsNone(self.h.nutrition_settings()["balance_from"])
        self.h.save_goal({"goal": "maintain"})
        self.h.save_balance_from({"from": "2026-02-01"})
        self.assertEqual(self.h.nutrition_settings(), {"goal": "maintain", "balance_from": "2026-02-01", "balance_period": "28d"})
        self.h.save_balance_from({"period": "month"})                               # only the period changes
        self.assertEqual(self.h.nutrition_settings()["balance_from"], "2026-02-01")
        self.assertEqual(self.h.nutrition_settings()["balance_period"], "month")
        import helio_transfer as ht
        files = ht.state_files(self.state)
        self.assertEqual(json.loads(files["nutrition.json"])["balance_from"], "2026-02-01")
        self.h.save_balance_from({"from": None})
        self.assertIsNone(self.h.nutrition_settings()["balance_from"])
        self.assertEqual(self.h.nutrition_settings()["goal"], "maintain")
        self.assertEqual(self.h.nutrition_settings()["balance_period"], "month")       # removing the date keeps the period
        for bad in ("2026-13-01", (DAY + timedelta(days=1)).isoformat(), "1999-12-31", 5, "yesterday"):
            with self.assertRaises(ValueError, msg=bad):
                self.h.save_balance_from({"from": bad})
        for bad in ({"period": "year"}, {"period": None}, {}):
            with self.assertRaises(ValueError, msg=bad):
                self.h.save_balance_from(bad)
        self.assertEqual(self.h.nutrition_settings()["balance_period"], "month")

    def test_damaged_date_is_ignored(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "nutrition.json").write_text('{"goal": "lose_fat", "balance_from": "2026-02-31", "balance_period": "year"}')
        self.assertIsNone(self.h.nutrition_settings()["balance_from"])
        self.assertEqual(self.h.nutrition_settings()["balance_period"], "28d")

    def test_profile_measurement_fields(self):
        ok = {"bf_pct": 24.5, "bf_date": "2026-02-10", "bf_method": "bia"}
        self.h.save_profile({"profile": ok})
        self.assertEqual({k: self.h.profile_load()[k] for k in ok}, ok)
        for bad in ({"bf_pct": 24.5}, {"bf_pct": 24.5, "bf_date": "2026-02-10"},
                    {**ok, "bf_pct": 2}, {**ok, "bf_pct": 80}, {**ok, "bf_method": "scale"},
                    {**ok, "bf_method": "skinfold"}, {**ok, "bf_method": "circumference"},
                    {**ok, "bf_date": (DAY + timedelta(days=1)).isoformat()}, {**ok, "bf_date": "10/02/2026"}):
            with self.assertRaises(ValueError, msg=bad):
                self.h.save_profile({"profile": bad})
        self.assertEqual(self.h.profile_load()["bf_method"], "bia")                 # a refused save changes nothing


class BalanceApi(StoreBase):
    def setUp(self):
        super().setUp()
        self.srv = self.h.ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, path, body=None, raw=None):
        hd = {"Content-Type": "application/json", "X-Helio": "1"}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, headers=hd if data is not None else {},
                                   method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_balance_endpoints(self):
        code, res = self.req(f"/api/balance?date={DAY.isoformat()}&period=28d")
        self.assertEqual(code, 200)
        self.assertEqual((res["period"], res["periods"]), ("28d", ["week", "prev_week", "28d", "month", "since"]))
        code, res = self.req("/api/balance?period=bogus")
        self.assertEqual((code, res["period"]), (200, "28d"))
        code, res = self.req("/api/balance_from", {"period": "month"})
        self.assertEqual((code, res["balance_period"]), (200, "month"))
        code, res = self.req(f"/api/balance?date={DAY.isoformat()}")                # without a period: the saved one
        self.assertEqual(res["period"], "month")
        code, res = self.req("/api/balance_from", {"period": "year"})
        self.assertEqual(code, 400)
        code, res = self.req("/api/balance?period=since")
        self.assertEqual(res["status"], "need_date")
        code, res = self.req("/api/balance_from", {"from": "2026-02-15"})
        self.assertEqual((code, res["balance_from"]), (200, "2026-02-15"))
        code, res = self.req(f"/api/balance?date={DAY.isoformat()}&period=since")
        self.assertEqual((res["since"], res["start"]), ("2026-02-15", "2026-02-15"))
        code, res = self.req("/api/balance_from", {"from": "2099-01-01"})
        self.assertEqual(code, 400)
        code, res = self.req("/api/balance_from", raw=b"x" * 300)                # above the 256-byte limit
        self.assertEqual(code, 400)
        self.assertEqual(self.h.nutrition_settings()["balance_from"], "2026-02-15")


if __name__ == "__main__":
    unittest.main()
