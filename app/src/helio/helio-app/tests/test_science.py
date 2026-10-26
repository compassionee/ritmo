"""Tests of the scientific formulas (helio_science.py) against the numbers published in the sources.

Every test states where the expected value comes from: if a formula is changed by mistake,
the result stops matching the study's.
"""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import helio_science as sci  # noqa: E402

P = dict(rest=55, hrmax=185, vo2max=45.8, bmr=1649, weight=74)


class Energy(unittest.TestCase):
    def test_weir_constant(self):
        # Weir 1949: kcal = 3.941 x VO2 + 1.106 x VCO2; with respiratory quotient 0.9 -> 4.936 kcal per litre of O2
        self.assertAlmostEqual(sci.KCAL_PER_L_O2, 4.9364, places=4)

    def test_ferri_marini_anchor_points(self):
        # Ferri Marini 2021: 40% of oxygen reserve matches 47.7% of heart rate reserve, 60% matches 67.2%
        self.assertAlmostEqual(sci.vo2r_fraction(0.477), 0.40, places=2)
        self.assertAlmostEqual(sci.vo2r_fraction(0.672), 0.60, places=2)

    def test_vo2r_limits(self):
        self.assertEqual(sci.vo2r_fraction(0.0), 0.0)
        self.assertEqual(sci.vo2r_fraction(0.05), 0.0)       # below the intercept (8.86%) there is no extra work
        self.assertLessEqual(sci.vo2r_fraction(1.0), 1.0)
        self.assertGreater(sci.vo2r_fraction(1.0), 0.9)

    def test_hrr_fraction(self):
        self.assertEqual(sci.hrr_fraction(55, 55, 185), 0.0)
        self.assertEqual(sci.hrr_fraction(185, 55, 185), 1.0)
        self.assertEqual(sci.hrr_fraction(120, 55, 185), 0.5)
        self.assertEqual(sci.hrr_fraction(40, 55, 185), 0.0)      # below rest
        self.assertEqual(sci.hrr_fraction(250, 55, 185), 1.0)     # above the maximum
        for bad in ((None, 55, 185), (120, None, 185), (120, 60, 60), (120, 70, 60)):
            self.assertEqual(sci.hrr_fraction(*bad), 0.0, bad)

    def test_vo2_rest_from_bmr(self):
        # 1649 kcal a day for 74 kg: about 3.1 mL/kg/min, close to the classic 3.5 of 1 MET
        v = sci.vo2_rest_ml(1649, 74)
        self.assertAlmostEqual(v, 1649 / 1440 / sci.KCAL_PER_L_O2 * 1000 / 74)
        self.assertTrue(2.8 < v < 3.6)

    def test_hr_active_kcal_worked_example(self):
        # 50% of heart rate reserve -> (50 - 8.86) / 0.972 = 42.3% of oxygen reserve
        f = (50 - 8.86) / 0.972 / 100
        reserve = 45.8 - sci.vo2_rest_ml(1649, 74)
        expected = f * reserve * 74 / 1000 * sci.KCAL_PER_L_O2
        self.assertAlmostEqual(sci.hr_active_kcal(120, **P), expected, places=6)
        self.assertTrue(6 < expected < 11)  # moderate-vigorous intensity for 74 kg

    def test_hr_active_kcal_shape(self):
        self.assertEqual(sci.hr_active_kcal(57, **P), 0.0)
        vals = [sci.hr_active_kcal(h, **P) for h in range(60, 192, 10)]
        self.assertEqual(vals, sorted(vals))                    # grows with heart rate
        heavier = sci.hr_active_kcal(150, **{**P, "weight": 95, "bmr": 1950})
        self.assertGreater(heavier, sci.hr_active_kcal(150, **P))
        fitter = sci.hr_active_kcal(150, **{**P, "vo2max": 60})
        self.assertGreater(fitter, sci.hr_active_kcal(150, **P))

    def test_lcda_equation(self):
        # Looney 2019: M = 1.44 + 1.94 S^0.43 + 0.24 S^4 (W/kg)
        self.assertAlmostEqual(sci.lcda_w_per_kg(0), 1.44)
        self.assertAlmostEqual(sci.lcda_w_per_kg(1.25), 1.44 + 1.94 * 1.25 ** 0.43 + 0.24 * 1.25 ** 4)
        self.assertEqual(sci.lcda_w_per_kg(3.0), sci.lcda_w_per_kg(2.0))   # valid up to 2 m/s
        self.assertEqual(sci.lcda_w_per_kg(-1), sci.lcda_w_per_kg(0))

    def test_walking_cost_close_to_classic_rule(self):
        # classic rule (ACSM): level walking about 0.5 kcal per kg per km above rest
        kcal = sum(sci.walk_kcal(100, 0.75, 74) for _ in range(100))  # 10,000 steps at 100 per minute = 7.5 km
        classic = 0.5 * 74 * 7.5
        self.assertAlmostEqual(kcal, classic, delta=classic * 0.12)

    def test_walking_cost_per_meter_is_u_shaped(self):
        per_m = lambda s: (sci.lcda_w_per_kg(s) - sci.lcda_w_per_kg(0)) / s
        self.assertLess(per_m(1.25), per_m(2.0))
        self.assertLess(per_m(1.25), per_m(0.5))

    def test_walk_kcal_cases(self):
        self.assertEqual(sci.walk_kcal(0, 0.75, 74), 0.0)
        self.assertEqual(sci.walk_kcal(-5, 0.75, 74), 0.0)
        few = sci.walk_kcal(20, 0.75, 74)
        self.assertGreater(few, 0)
        # a few scattered steps cost as much as the same distance at a natural pace (1.25 m/s), not as very slow walking:
        # 100 steps of 75 cm in a minute are exactly 1.25 m/s, so 20 steps cost a fifth
        self.assertAlmostEqual(few, sci.walk_kcal(100, 0.75, 74) / 5, places=9)
        steps = [sci.walk_kcal(n, 0.75, 74) for n in (10, 40, 80, 120, 160)]
        self.assertEqual(steps, sorted(steps))

    def test_kcal_range(self):
        self.assertEqual(sci.kcal_range(400), [300, 500])
        self.assertEqual(sci.kcal_range(300, sci.PANDOLF_UNDER, True), [300, 399])
        self.assertEqual(sci.kcal_range(0), [0, 0])
        self.assertEqual(sci.BMR_ERR, 0.10)      # Madden 2016: Mifflin within ±10% in 66% of cases
        self.assertEqual(sci.ACTIVE_ERR, 0.25)
        self.assertEqual(sci.PANDOLF_UNDER, 0.33)  # Drain 2017: underestimates by up to 33%


class TrainingLoad(unittest.TestCase):
    def test_banister_male(self):
        # Morton 1990: one minute at 60% of reserve is worth 0.6 x 0.64 x e^(1.92 x 0.6)
        hr = 55 + 0.6 * (185 - 55)
        one = sci.banister_trimp([hr], 55, 185, "M")
        self.assertAlmostEqual(one, 0.6 * 0.64 * math.exp(1.92 * 0.6), places=6)
        self.assertAlmostEqual(sci.banister_trimp([hr] * 60, 55, 185, "M"), 60 * one, places=6)

    def test_banister_female_and_unknown(self):
        hr = 55 + 0.6 * (185 - 55)
        self.assertAlmostEqual(sci.banister_trimp([hr], 55, 185, "F"), 0.6 * 0.86 * math.exp(1.67 * 0.6), places=6)
        m, f = sci.banister_trimp([hr], 55, 185, "M"), sci.banister_trimp([hr], 55, 185, "F")
        self.assertTrue(min(m, f) <= sci.banister_trimp([hr], 55, 185, None) <= max(m, f))

    def test_banister_weights_intensity(self):
        # two minutes at 40% weigh less than one at 80%: Banister's TRIMP grows exponentially
        low = sci.banister_trimp([55 + 0.4 * 130] * 2, 55, 185, "M")
        high = sci.banister_trimp([55 + 0.8 * 130], 55, 185, "M")
        self.assertLess(low, high)

    def test_banister_ignores_rest_and_missing(self):
        self.assertEqual(sci.banister_trimp([55, 50, None], 55, 185, "M"), 0.0)
        self.assertEqual(sci.banister_trimp([], 55, 185, "M"), 0.0)


class Fitness(unittest.TestCase):
    def test_uth(self):
        # Uth 2004: VO2max = 15.3 x HRmax / HRrest
        self.assertAlmostEqual(sci.uth_vo2max(185, 55), 15.3 * 185 / 55)
        self.assertAlmostEqual(sci.uth_vo2max(200, 50), 61.2)
        for bad in ((None, 57), (185, None), (185, 0)):
            self.assertIsNone(sci.uth_vo2max(*bad))
        self.assertEqual(sci.VO2MAX_SEE, 7.0)   # Esco 2012: standard error 6.9-7.9 mL/kg/min

    def test_altitude(self):
        # Wehrlin & Hallén 2006: -6.3% every 1000 m above 300 m (from 66 to 55 mL/kg/min between 300 and 2800 m)
        self.assertEqual(sci.altitude_fraction(0), 1.0)
        self.assertEqual(sci.altitude_fraction(300), 1.0)
        self.assertAlmostEqual(sci.altitude_fraction(1300), 0.937)
        at2800 = sci.altitude_fraction(2800)
        self.assertAlmostEqual(at2800, 1 - 0.063 * 2.5)
        self.assertAlmostEqual(66 * at2800, 55.6, delta=1.0)       # 55 in the study
        self.assertAlmostEqual(sci.altitude_fraction(4500), 1 - 0.063 * 4.2, places=6)
        self.assertGreaterEqual(sci.altitude_fraction(30000), 0.0)

    def test_met(self):
        self.assertEqual(sci.MET_ML, 3.5)


class Strength(unittest.TestCase):
    def test_brzycki(self):
        self.assertAlmostEqual(sci.brzycki_1rm(100, 5), 112.5)
        self.assertAlmostEqual(sci.brzycki_1rm(100, 1), 100)
        self.assertAlmostEqual(sci.brzycki_1rm(74, 8), 74 * 36 / 29)
        self.assertAlmostEqual(sci.brzycki_1rm(97, 10), 97 * 36 / 27)

    def test_brzycki_limits(self):
        # Reynolds 2006: beyond 10 repetitions the estimate is not reliable
        self.assertIsNone(sci.brzycki_1rm(100, 11))
        self.assertIsNone(sci.brzycki_1rm(100, 0))
        self.assertIsNone(sci.brzycki_1rm(0, 5))
        self.assertIsNone(sci.brzycki_1rm(None, 5))
        self.assertEqual(sci.MAX_REPS_1RM, 10)

    def test_more_reps_same_load_means_stronger(self):
        vals = [sci.brzycki_1rm(80, r) for r in range(1, 11)]
        self.assertEqual(vals, sorted(vals))


class SleepRegularity(unittest.TestCase):
    @staticmethod
    def day(sleep_from, sleep_to, n=1440):
        return [0 if sleep_from <= m < sleep_to else 1 for m in range(n)]

    def test_identical_days_score_100(self):
        days = [self.day(0, 420) for _ in range(14)]
        v, pairs = sci.sleep_regularity_index(days)
        self.assertEqual(v, 100)
        self.assertEqual(pairs, 13 * 1440)

    def test_opposite_days_score_minus_100(self):
        days = [self.day(0, 720), self.day(720, 1440)] * 4
        self.assertEqual(sci.sleep_regularity_index(days)[0], -100)

    def test_random_states_near_zero(self):
        rnd = random.Random(7)
        days = [[rnd.randint(0, 1) for _ in range(1440)] for _ in range(14)]
        self.assertLess(abs(sci.sleep_regularity_index(days)[0]), 5)

    def test_shifted_bedtime_lowers_score(self):
        regular = [self.day(0, 420) for _ in range(14)]
        shifted = [self.day(0, 420) if i % 2 else self.day(120, 540) for i in range(14)]
        a, b = sci.sleep_regularity_index(regular)[0], sci.sleep_regularity_index(shifted)[0]
        self.assertLess(b, a)
        # 2 hours shifted every day: 240 minutes out of 1440 differ -> 200 x (1200/1440) - 100 = 66.7
        self.assertAlmostEqual(b, 200 * (1440 - 240) / 1440 - 100, places=6)

    def test_missing_data_is_excluded(self):
        a = self.day(0, 420)
        gap = [None] * 600 + a[600:]
        v, pairs = sci.sleep_regularity_index([a, gap, a])
        self.assertEqual(v, 100)
        self.assertEqual(pairs, 2 * (1440 - 600))

    def test_not_enough_days(self):
        self.assertEqual(sci.sleep_regularity_index([self.day(0, 420)]), (None, 0))
        self.assertEqual(sci.sleep_regularity_index([]), (None, 0))
        self.assertEqual(sci.sleep_regularity_index([[None] * 1440, [None] * 1440]), (None, 0))

    def test_circular_mean_across_midnight(self):
        mean, sd = sci.circular_sd_minutes([23 * 60 + 30, 30])  # 23:30 and 00:30 -> mean midnight
        self.assertTrue(mean in (0, 1440) or mean < 2 or mean > 1438)
        self.assertTrue(25 <= sd <= 35)
        self.assertEqual(sci.circular_sd_minutes([420, 420, 420])[1], 0)
        self.assertEqual(sci.circular_sd_minutes([420]), (None, None))
        mean2, sd2 = sci.circular_sd_minutes([400, 420, 440])
        self.assertEqual(mean2, 420)
        self.assertTrue(10 < sd2 < 25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
