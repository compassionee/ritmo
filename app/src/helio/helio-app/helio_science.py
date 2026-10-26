"""Helio's scientific formulas, without database access (easy to check in the tests).

Every function states its source. Sources are chosen among meta-analyses, guidelines and independent
studies; those with declared conflicts of interest have been replaced (see SOURCES
in helio_app.py, where each source states its funding and conflicts).
"""
import math

# ---------------------------------------------------------------- calories
# 1 litre of oxygen consumed releases about 4.94 kcal with a respiratory quotient of 0.9
# (Weir 1949 equation: kcal = 3.941 x VO2 + 1.106 x VCO2).
KCAL_PER_L_O2 = 3.941 + 1.106 * 0.9

# Ferri Marini et al. 2021 (HERITAGE study, 737 adults): the fraction of heart rate reserve does not match
# the fraction of oxygen reserve: %HRR = 0.972 x %VO2R + 8.86 (mean individual error 9.25 points).
HRR_SLOPE, HRR_INTERCEPT, HRR_RMSE = 0.972, 8.86, 9.25

# Error margins used for the plausible calorie range
BMR_ERR = 0.10       # Mifflin-St Jeor: on average 66% of people within ±10% (Madden 2016)
ACTIVE_ERR = 0.25    # heart rate reserve ±9 points (Ferri Marini 2021) and VO2max ±7 out of ~50 (Esco 2012): ~±25%
PANDOLF_UNDER = 0.33  # Pandolf underestimates modern load carriage by 12-33% (Drain 2017): range only upwards


def hrr_fraction(hr, rest, hrmax):
    """Fraction of heart rate reserve (0 = rest, 1 = maximum)."""
    if hr is None or not rest or not hrmax or hrmax <= rest:
        return 0.0
    return max(0.0, min(1.0, (hr - rest) / (hrmax - rest)))


def vo2r_fraction(hrr):
    """Fraction of oxygen reserve matching a fraction of heart rate reserve (Ferri Marini 2021)."""
    return max(0.0, min(1.0, (100 * hrr - HRR_INTERCEPT) / HRR_SLOPE / 100))


def vo2_rest_ml(bmr, weight):
    """Resting oxygen uptake (mL/kg/min) derived from basal metabolism."""
    return bmr / 1440 / KCAL_PER_L_O2 * 1000 / weight


def hr_active_kcal(hr, rest, hrmax, vo2max, bmr, weight):
    """Active calories of one minute from heart rate: heart rate reserve -> oxygen reserve -> kcal.

    VO2 = resting VO2 + VO2R fraction x (VO2max - resting VO2); only the part above rest counts.
    """
    f = vo2r_fraction(hrr_fraction(hr, rest, hrmax))
    reserve = max(0.0, vo2max - vo2_rest_ml(bmr, weight))
    return f * reserve * weight / 1000 * KCAL_PER_L_O2


def lcda_w_per_kg(speed):
    """Cost of level walking without load (W/kg), LCDA equation by Looney et al. 2019 (48 studies).

    M = 1.44 + 1.94 x S^0.43 + 0.24 x S^4, with S in m/s (valid up to 2 m/s). At S = 0 it is the standing cost.
    """
    s = max(0.0, min(2.0, speed))
    return 1.44 + 1.94 * s ** 0.43 + 0.24 * s ** 4


def walk_kcal(steps, stride_m, weight):
    """Walking calories above standing for the steps of one minute (Looney 2019).

    The cost per metre uses the minute's speed if walking is continuous (at least 60 steps),
    otherwise 1.25 m/s, the natural pace: a few scattered steps in a minute are not slow walking.
    """
    if steps <= 0:
        return 0.0
    dist = steps * stride_m
    speed = dist / 60 if steps >= 60 else 1.25
    speed = max(1.0, min(2.0, speed))
    per_m = (lcda_w_per_kg(speed) - lcda_w_per_kg(0)) / speed  # J/kg/m
    return per_m * dist * weight / 4184


def pandolf_kcal_min(steps_min, stride_m, weight, load_kg, bmr, grade=0.0, eta=1.0):
    """Pandolf et al. 1977: cost of walking with a load (W) in kcal/min, above basal metabolism."""
    w, l = weight, load_kg
    v = steps_min * stride_m / 60.0
    watts = 1.5 * w + 2.0 * (w + l) * (l / w) ** 2 + eta * (w + l) * (1.5 * v * v + 0.35 * v * grade)
    return max(0.0, watts * 0.01433 - bmr / 1440)


def kcal_range(kcal, err=ACTIVE_ERR, up_only=False):
    """Plausible range [low, high] of a calorie estimate."""
    if up_only:
        return [round(kcal), round(kcal * (1 + err))]
    return [round(kcal * (1 - err)), round(kcal * (1 + err))]


# ---------------------------------------------------------------- training load
def banister_trimp(minutes_hr, rest, hrmax, sex):
    """Banister's TRIMP (Morton, Fitz-Clarke, Banister 1990): sum over every minute of x · k·e^(b·x).

    x = fraction of heart rate reserve; men k = 0.64, b = 1.92; women k = 0.86, b = 1.67.
    With sex not given the mean of the two is used.
    """
    k, b = {"M": (0.64, 1.92), "F": (0.86, 1.67)}.get(sex, (0.75, 1.795))
    tot = 0.0
    for hr in minutes_hr:
        if hr is None:
            continue
        x = hrr_fraction(hr, rest, hrmax)
        tot += x * k * math.exp(b * x)
    return tot


# ---------------------------------------------------------------- fitness
UTH_FACTOR = 15.3     # Uth et al. 2004
VO2MAX_SEE = 7.0      # standard error in the independent validation by Esco et al. 2012 (6.9-7.9 mL/kg/min)
ALT_DROP_PER_KM = 0.063  # Wehrlin & Hallén 2006: -6.3% of VO2max every 1000 m above 300 m (measured up to 2800 m)
MET_ML = 3.5          # 1 MET = 3.5 mL of oxygen per kg per minute


def uth_vo2max(hrmax, hrrest):
    """Estimated VO2max (mL/kg/min) from the ratio of maximum to resting heart rate (Uth et al. 2004)."""
    if not hrmax or not hrrest or hrrest <= 0:
        return None
    return UTH_FACTOR * hrmax / hrrest


def altitude_fraction(alt_m):
    """Fraction of sea-level VO2max left at altitude (Wehrlin & Hallén 2006, straight line above 300 m)."""
    km = max(0.0, alt_m / 1000 - 0.3)
    return max(0.0, 1 - ALT_DROP_PER_KM * km)


# ---------------------------------------------------------------- strength
MAX_REPS_1RM = 10  # Reynolds 2006: do not use more than 10 repetitions to estimate the one-repetition maximum


def brzycki_1rm(load, reps):
    """Estimated one-repetition maximum (Brzycki): load x 36 / (37 - reps), valid up to 10 reps."""
    if not load or not reps or reps < 1 or reps > MAX_REPS_1RM:
        return None
    return load * 36 / (37 - reps)


# ---------------------------------------------------------------- sleep regularity
def sleep_regularity_index(days):
    """Sleep Regularity Index (formula in Fischer, Klerman, Phillips 2021).

    days: list of days, each a list of states per minute (0 = asleep, 1 = awake, None = missing),
    all of the same length. Probability that the state is the same 24 hours apart, reported
    from -100 to 100: 100 = identical times every day, 0 = no relationship. Only pairs where
    both minutes have data are used. Returns (SRI, valid pairs).
    """
    same = valid = 0
    for a, b in zip(days, days[1:]):
        for x, y in zip(a, b):
            if x is None or y is None:
                continue
            valid += 1
            same += x == y
    if not valid:
        return None, 0
    return 200 * same / valid - 100, valid


def circular_sd_minutes(minutes):
    """Standard deviation of times of day (minutes from midnight), taking the 24-hour wrap into account."""
    if len(minutes) < 2:
        return None, None
    ang = [2 * math.pi * m / 1440 for m in minutes]
    c = sum(math.cos(a) for a in ang) / len(ang)
    s = sum(math.sin(a) for a in ang) / len(ang)
    r = min(1.0, math.hypot(c, s))
    mean_min = (math.degrees(math.atan2(s, c)) % 360) / 360 * 1440
    sd = math.sqrt(-2 * math.log(r)) / (2 * math.pi) * 1440 if r > 0 else 720.0
    return round(mean_min), round(sd)


# ---------------------------------------------------------------- nutrition
# Regulation (EU) 1169/2011, Annex XIV: factors to compute the energy of a food (kcal per gram).
# On the European label polyols are part of carbohydrates and fibre is separate.
ATWATER = {"carbs": 4.0, "protein": 4.0, "fat": 9.0, "fiber": 2.0, "polyols": 2.4, "alcohol": 7.0}
MACROS = ("protein", "carbs", "fat", "fiber", "sat", "salt", "polyols", "alcohol")

# Hall 2008: energy density of what is lost or gained. Fat gives the smallest weight change for
# a given balance, lean mass the largest.
FAT_KCAL_PER_KG = 39.5e3 / 4.184    # 39.5 MJ per kg of fat: about 9,441 kcal
LEAN_KCAL_PER_KG = 7.6e3 / 4.184    # 7.6 MJ per kg of lean mass: about 1,816 kcal


def atwater_kcal(v):
    """Energy (kcal) from nutrients in grams with the EU factors: 4 carbohydrates (excluding polyols) and protein,
    9 fat, 2 fibre, 2.4 polyols, 7 alcohol."""
    pol = v.get("polyols") or 0.0
    return (ATWATER["carbs"] * max(0.0, (v.get("carbs") or 0.0) - pol) + ATWATER["polyols"] * pol
            + ATWATER["protein"] * (v.get("protein") or 0.0) + ATWATER["fat"] * (v.get("fat") or 0.0)
            + ATWATER["fiber"] * (v.get("fiber") or 0.0) + ATWATER["alcohol"] * (v.get("alcohol") or 0.0))


def label_tolerance(nutrient, v):
    """Tolerance (g per 100 g) between the label value and the real content, measurement included.

    European Commission 2012 guidance on regulation 1169/2011: carbohydrates, protein, fibre (and polyols)
    ±2 g below 10 g, ±20% between 10 and 40 g, ±8 g above 40 g; fat ±1.5 g below 10 g, then as above;
    saturated fat ±0.8 g below 4 g, then ±20%; salt ±0.375 g below 1.25 g, then ±20%. There is no value for alcohol.
    """
    v = max(0.0, v or 0.0)
    if nutrient in ("protein", "carbs", "fiber", "polyols"):
        return 2.0 if v < 10 else 0.2 * v if v <= 40 else 8.0
    if nutrient == "fat":
        return 1.5 if v < 10 else 0.2 * v if v <= 40 else 8.0
    if nutrient == "sat":
        return 0.8 if v < 4 else 0.2 * v
    if nutrient == "salt":
        return 0.375 if v < 1.25 else 0.2 * v
    return 0.0


def food_range(per100):
    """Plausible minimum and maximum values of a food (per 100 g) according to the EU tolerances.

    Kcal follow the nutrients: the declared kcal are scaled with the energy computed at the two extremes.
    """
    lo, hi = {}, {}
    for k in MACROS:
        if per100.get(k) is None:
            continue
        t = label_tolerance(k, per100[k])
        lo[k], hi[k] = max(0.0, per100[k] - t), min(100.0, per100[k] + t)
    if "polyols" in lo:  # polyols are part of carbohydrates
        lo["polyols"], hi["polyols"] = min(lo["polyols"], lo["carbs"]), min(hi["polyols"], hi["carbs"])
    e = atwater_kcal(per100)
    kcal = per100.get("kcal") or 0.0
    if e > 0:
        lo["kcal"], hi["kcal"] = kcal * atwater_kcal(lo) / e, kcal * atwater_kcal(hi) / e
    else:
        lo["kcal"] = hi["kcal"] = kcal
    return lo, hi


def rounding_tol(v):
    """Maximum error caused by label rounding (same EU 2012 guidance): to the gram from 10 g up,
    to the tenth between 0.5 and 10 g, "0" or "<0.5 g" below 0.5 g."""
    if v >= 10:
        return 0.5
    if v >= 0.5:
        return 0.05
    return 0.5


def label_energy_check(per100):
    """Do the label kcal match the nutrients? Recomputed with the EU factors and compared with the maximum
    deviation caused by rounding (nutrients and 0.5 kcal of energy). Used to catch typos."""
    computed = atwater_kcal(per100)
    tol = 0.5 + sum(ATWATER[k] * rounding_tol(per100.get(k) or 0.0) for k in ("carbs", "protein", "fat", "fiber"))
    tol += sum(ATWATER[k] * rounding_tol(per100[k]) for k in ("polyols", "alcohol") if per100.get(k))
    declared = per100.get("kcal") or 0.0
    diff = declared - computed
    no_fiber = computed - ATWATER["fiber"] * (per100.get("fiber") or 0.0)
    return {"declared": round(declared, 1), "computed": round(computed, 1), "diff": round(diff, 1), "tol": round(tol, 1),
            "ok": abs(diff) <= tol,
            # some labels (old or non-EU) do not count the energy of fibre
            "ok_without_fiber": abs(declared - no_fiber) <= tol and (per100.get("fiber") or 0) > 0}


def judge(value, lo, hi, minimum=None, maximum=None):
    """Comparison with a threshold taking the range into account: "ok" if the central value respects it,
    "certain" (certain) if the whole range is beyond it, otherwise "probable" (probable)."""
    if minimum is not None and value < minimum:
        return "certain" if hi < minimum else "probable"
    if maximum is not None and value > maximum:
        return "certain" if lo > maximum else "probable"
    return "ok"


def energy_parts(v):
    """Energy (kcal) of each nutrient group, with the EU factors."""
    pol = v.get("polyols") or 0.0
    return {"carbs": ATWATER["carbs"] * max(0.0, (v.get("carbs") or 0.0) - pol) + ATWATER["polyols"] * pol,
            "protein": ATWATER["protein"] * (v.get("protein") or 0.0), "fat": ATWATER["fat"] * (v.get("fat") or 0.0),
            "fiber": ATWATER["fiber"] * (v.get("fiber") or 0.0), "alcohol": ATWATER["alcohol"] * (v.get("alcohol") or 0.0)}


def energy_shares(tot, lo, hi, sat_lo=None, sat_hi=None):
    """Share of calories (%) from carbohydrates, protein and fat (and saturated fat, if known), with the range.

    The minimum of a share uses that nutrient at its minimum and everything else at its maximum, the maximum the opposite.
    """
    c, cl, ch = energy_parts(tot), energy_parts(lo), energy_parts(hi)
    total = sum(c.values())
    if total <= 0:
        return None
    out = {}
    for k in ("carbs", "protein", "fat"):
        r_lo = sum(v for kk, v in cl.items() if kk != k)
        r_hi = sum(v for kk, v in ch.items() if kk != k)
        out[k] = {"value": 100 * c[k] / total,
                  "lo": 100 * cl[k] / (cl[k] + r_hi) if cl[k] + r_hi > 0 else 0.0,
                  "hi": 100 * ch[k] / (ch[k] + r_lo) if ch[k] + r_lo > 0 else 0.0}
    if tot.get("sat") is not None:
        s, s_lo, s_hi = 9 * tot["sat"], 9 * (sat_lo if sat_lo is not None else lo.get("sat", 0)), \
            9 * (sat_hi if sat_hi is not None else hi.get("sat", 0))
        nonfat_lo = sum(v for kk, v in cl.items() if kk != "fat")
        nonfat_hi = sum(v for kk, v in ch.items() if kk != "fat")
        r_lo = nonfat_lo + max(0.0, cl["fat"] - s_hi)
        r_hi = nonfat_hi + max(0.0, ch["fat"] - s_lo)
        out["sat"] = {"value": 100 * s / total,
                      "lo": 100 * s_lo / (s_lo + r_hi) if s_lo + r_hi > 0 else 0.0,
                      "hi": 100 * s_hi / (s_hi + r_lo) if s_hi + r_lo > 0 else 0.0}
    return out


# 0.975 quantile of Student's t for the 95% confidence interval (1-30 degrees of freedom)
T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
        2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t975(df):
    """Student's t at 97.5%: table up to 30 degrees of freedom, then the lower-limit value (conservative)."""
    if df < 1:
        return None
    if df <= 30:
        return T975[df - 1]
    return 2.042 if df <= 40 else 2.021 if df <= 60 else 2.000 if df <= 120 else 1.980


def linear_trend(xs, ys):
    """Least-squares line: slope with its 95% confidence interval (at least 3 points needed)."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    resid = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    se = math.sqrt(resid / (n - 2) / sxx)
    t = t975(n - 2)
    return {"slope": b, "lo": b - t * se, "hi": b + t * se, "se": se, "n": n}


def weight_rate_bounds(b_lo, b_hi):
    """Possible weight change (kg per day) with a balance between b_lo and b_hi kcal per day (Hall 2008).

    With a deficit at least balance/9,441 kg is lost (fat only) and at most balance/1,816 kg (lean mass only);
    with a surplus the same, upwards. Water and glycogen are left out: that is why the check is only indicative.
    """
    def low(b):
        return b / LEAN_KCAL_PER_KG if b < 0 else b / FAT_KCAL_PER_KG

    def high(b):
        return b / FAT_KCAL_PER_KG if b < 0 else b / LEAN_KCAL_PER_KG

    return low(b_lo), high(b_hi)


def min_weight_change(balance_kcal):
    """Minimum weight change (kg) for a cumulative balance in kcal: all of it as fat (Hall 2008)."""
    return balance_kcal / FAT_KCAL_PER_KG
