"""Par-curve bootstrapping, key-rate DV01 and curve scenarios for semiannual bullet bonds.

The par curve (semiannual bond-equivalent yields at key tenors) is interpolated linearly to every
half-year, then bootstrapped into discount factors:

    DF_k = (1 - (y_k / 2) * sum_{j<k} DF_j) / (1 + y_k / 2)

Each bond is priced off the curve plus a constant spread. The spread is solved once, at the base curve,
so that the bond reprices exactly to its own yield. It is then held fixed while the curve moves, which
is the usual spread-constant convention for key-rate risk.

Key-rate DV01 bumps one par-curve tenor at a time with triangular weights (1 at the bumped tenor,
falling linearly to 0 at its neighbours) and fully reprices every cash flow.

Sign convention: DV01 is the dollar LOSS for a +1bp move, so a long bond has positive DV01.
This matches `summary()["dv01"]` in portfolio.py.
"""

import csv
import math

from .pricing import cashflow_times, price

FREQ = 2
MAX_PERIODS = 120   # 60 years of semiannual periods; beyond the last tenor the curve is flat


def load_par_curve(path):
    """Read data/treasury_par_curve.csv. Returns (tenors, par_yields) with yields as decimals."""
    tenors, yields = [], []
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        missing = [c for c in ("tenor_years", "par_yield") if c not in (rd.fieldnames or [])]
        if missing:
            raise ValueError(f"par curve CSV missing columns: {missing}")
        for row in rd:
            tenors.append(float(row["tenor_years"]))
            yields.append(float(row["par_yield"]))
    if len(tenors) < 2:
        raise ValueError("par curve needs at least two tenors")
    if any(b <= a for a, b in zip(tenors, tenors[1:])):
        raise ValueError("par curve tenors must be strictly increasing")
    return tenors, yields


def interp(xs, ys, x):
    """Linear interpolation, flat beyond the ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            w = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + w * (ys[i + 1] - ys[i])
    raise ValueError("unreachable")


def discount_factors(tenors, par, max_periods=MAX_PERIODS):
    """DF[k] for k = 0..max_periods (DF[0] = 1), bootstrapped from the par curve."""
    df = [1.0]
    for k in range(1, max_periods + 1):
        c = interp(tenors, par, k / FREQ) / FREQ
        annuity = sum(df[1:k])
        df.append((1.0 - c * annuity) / (1.0 + c))
    return df


def zero_rates(tenors, par, max_periods=MAX_PERIODS):
    """[(years, zero rate)] at every half-year, as semiannual bond-equivalent decimals.

    From DF_k = (1 + z_k / 2)^(-k), so z_k = 2 * (DF_k^(-1/k) - 1).
    """
    df = discount_factors(tenors, par, max_periods)
    return [(k / FREQ, 2.0 * (df[k] ** (-1.0 / k) - 1.0)) for k in range(1, max_periods + 1)]


def bump_par(tenors, par, j, bp):
    """Par curve with a triangular bump of `bp` basis points centred on tenor index j."""
    out = list(par)
    dy = bp / 10_000.0
    for i, t in enumerate(tenors):
        if i == j:
            w = 1.0
        elif i < j and j > 0:
            w = max(0.0, (t - tenors[j - 1]) / (tenors[j] - tenors[j - 1])) if t > tenors[j - 1] else 0.0
        elif i > j and j < len(tenors) - 1:
            w = max(0.0, (tenors[j + 1] - t) / (tenors[j + 1] - tenors[j])) if t < tenors[j + 1] else 0.0
        else:
            w = 0.0
        out[i] = par[i] + dy * w
    return out


def _check(bond):
    if bond.frequency != FREQ:
        raise ValueError(f"{bond.id}: curve pricing supports semiannual bonds only (frequency=2)")
    if bond.periods > MAX_PERIODS:
        raise ValueError(f"{bond.id}: maturity beyond {MAX_PERIODS // FREQ} years")


def curve_price(bond, df, spread=0.0):
    """Price per 100 par off a discount-factor curve plus a constant spread."""
    return sum(cf * df[k] / (1.0 + spread / FREQ) ** k for k, _, cf in cashflow_times(bond))


def calibrated_spread(bond, df, lo=-0.2, hi=1.0, tol=1e-12):
    """Constant spread that makes the curve price equal the bond's price at its own yield."""
    _check(bond)
    target = price(bond)
    f_lo = curve_price(bond, df, lo) - target
    f_hi = curve_price(bond, df, hi) - target
    if f_lo * f_hi > 0:
        raise ValueError(f"{bond.id}: no spread in [{lo}, {hi}] reprices to its yield")
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        f_mid = curve_price(bond, df, mid) - target
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def base_spreads(bonds, tenors, par):
    """Spread per bond, as a list aligned with `bonds` (index, not id, so duplicate ids stay separate)."""
    df = discount_factors(tenors, par)
    return [calibrated_spread(b, df) for b in bonds]


def portfolio_mv(bonds, df, spreads):
    return sum(b.par * curve_price(b, df, s) / 100.0 for b, s in zip(bonds, spreads))


def key_rate_dv01(bonds, tenors, par, bp=1.0):
    """[(tenor, dollar loss for +1bp at that tenor)] with spreads held at their base values."""
    spreads = base_spreads(bonds, tenors, par)
    out = []
    for j, t in enumerate(tenors):
        up = portfolio_mv(bonds, discount_factors(tenors, bump_par(tenors, par, j, bp)), spreads)
        dn = portfolio_mv(bonds, discount_factors(tenors, bump_par(tenors, par, j, -bp)), spreads)
        out.append((t, -(up - dn) / (2.0 * bp)))
    return out


# Curve moves as {tenor in years: shift in bp}, interpolated linearly between the listed tenors.
SCENARIOS = {
    "Parallel +100bp": {0.5: 100, 30: 100},
    "Parallel -100bp": {0.5: -100, 30: -100},
    "Bear steepener": {0.5: 25, 2: 30, 5: 55, 10: 85, 30: 90},
    "Bull flattener": {0.5: -60, 2: -55, 5: -40, 10: -20, 30: -10},
    "Front-end shock +50bp": {0.5: 50, 2: 45, 5: 20, 10: 5, 30: 0},
}


def _scenario_pnls(bonds, tenors, par, shape):
    """Dollar P&L per bond, in the order of `bonds`."""
    spreads = base_spreads(bonds, tenors, par)
    pts = sorted(shape)
    shifts = [interp(pts, [shape[t] for t in pts], t) for t in tenors]
    shocked = [p + s / 10_000.0 for p, s in zip(par, shifts)]
    df0 = discount_factors(tenors, par)
    df1 = discount_factors(tenors, shocked)
    return [b.par * (curve_price(b, df1, s) - curve_price(b, df0, s)) / 100.0 for b, s in zip(bonds, spreads)]


def scenario_pnl_by_bond(bonds, tenors, par, shape):
    """{bond id: dollar P&L} from fully repricing each bond after shifting the par curve by `shape`.

    Keyed by id, so duplicate ids collapse to one entry here; use scenario_pnl for totals.
    """
    return dict(zip((b.id for b in bonds), _scenario_pnls(bonds, tenors, par, shape)))


def scenario_pnl(bonds, tenors, par, shape):
    """Dollar P&L from fully repricing every bond after shifting the par curve by `shape`."""
    return sum(_scenario_pnls(bonds, tenors, par, shape))


def effective_duration_convexity(bonds, tenors, par, bp=10.0):
    """(effective duration, effective convexity) of the portfolio by full repricing off the curve.

    Parallel par-curve shifts of +/-bp with spreads held fixed:
    D = (P- - P+) / (2 P0 dy),  C = (P+ + P- - 2 P0) / (P0 dy^2). Unlike the analytic yield-based
    figures this works on the curve, so it reflects the curve's shape.
    """
    if not bonds:
        raise ValueError("no bonds")
    spreads = base_spreads(bonds, tenors, par)
    dy = bp / 10_000.0
    mv = [portfolio_mv(bonds, discount_factors(tenors, [p + s * dy for p in par]), spreads) for s in (0, 1, -1)]
    p0, up, dn = mv
    return (dn - up) / (2.0 * p0 * dy), (up + dn - 2.0 * p0) / (p0 * dy * dy)


# Basel IRRBB standardised shocks in bp. IRRBB_USD is the 2016 calibration (BCBS d368, Annex 2).
# IRRBB_2024 is the recalibration in force from 2026-01-01 (BCBS d578): (parallel, short, long) per
# currency, rounded to 25bp. USD moved long 150 -> 225. Use irrbb_scenarios(*IRRBB_2024["USD"]).
IRRBB_USD = {"parallel_bp": 200, "short_bp": 300, "long_bp": 150}
IRRBB_2024 = {
    "ARS": (400, 500, 300), "AUD": (350, 425, 300), "BRL": (400, 500, 300), "CAD": (200, 275, 175),
    "CHF": (175, 250, 200), "CNY": (225, 300, 150), "EUR": (225, 350, 200), "GBP": (275, 425, 250),
    "HKD": (225, 375, 200), "IDR": (400, 500, 300), "INR": (325, 475, 225), "JPY": (100, 100, 100),
    "KRW": (225, 350, 225), "MXN": (400, 500, 200), "RUB": (400, 500, 300), "SAR": (275, 375, 250),
    "SEK": (275, 425, 200), "SGD": (175, 250, 225), "TRY": (400, 500, 300), "USD": (200, 300, 225),
    "ZAR": (325, 500, 300),
}
IRRBB_TENORS = (0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30)


def irrbb_scenarios(parallel_bp=200, short_bp=300, long_bp=150, x=4.0, tenors=IRRBB_TENORS):
    """The six Basel IRRBB scenarios as {name: {tenor: shift in bp}}, usable with scenario_pnl.

    short(t) = S e^(-t/x), long(t) = L (1 - e^(-t/x)),
    steepener = -0.65 |short| + 0.90 |long|, flattener = 0.80 |short| - 0.60 |long|.
    """
    short = {t: short_bp * math.exp(-t / x) for t in tenors}
    long_ = {t: long_bp * (1.0 - math.exp(-t / x)) for t in tenors}
    return {
        "Parallel up": {t: float(parallel_bp) for t in tenors},
        "Parallel down": {t: -float(parallel_bp) for t in tenors},
        "Steepener": {t: -0.65 * short[t] + 0.90 * long_[t] for t in tenors},
        "Flattener": {t: 0.80 * short[t] - 0.60 * long_[t] for t in tenors},
        "Short rates up": dict(short),
        "Short rates down": {t: -v for t, v in short.items()},
    }


def eba_floor(t):
    """EBA 2018 post-shock rate floor: -1% at t=0 rising 5bp a year to 0 at 20y. Basel leaves floors
    to national supervisors (any floor <= 0); a flat 0 is `lambda t: 0.0`."""
    return min(0.0, -0.01 + 0.0005 * t)


def floor_shape(shape, tenors, par, floor=eba_floor):
    """Shift per curve tenor (bp) with each shocked par rate held at or above floor(t).

    `shape` is a {tenor: bp} scenario; floors only bind on down-shocks of low rates.
    """
    pts = sorted(shape)
    out = {}
    for t, p in zip(tenors, par):
        shift = interp(pts, [shape[x] for x in pts], t)
        out[t] = max(shift, min(0.0, (floor(t) - p) * 10_000.0))  # never lifts a rate already below the floor
    return out
