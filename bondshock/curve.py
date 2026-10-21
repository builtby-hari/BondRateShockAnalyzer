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

from .pricing import cashflow_times, price

FREQ = 2
MAX_PERIODS = 120   # 60 years of semiannual periods; beyond the last tenor the curve is flat


def load_par_curve(path):
    """Read data/treasury_par_curve.csv. Returns (tenors, par_yields) with yields as decimals."""
    tenors, yields = [], []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
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
    df = discount_factors(tenors, par)
    return {b.id: calibrated_spread(b, df) for b in bonds}


def portfolio_mv(bonds, df, spreads):
    return sum(b.par * curve_price(b, df, spreads[b.id]) / 100.0 for b in bonds)


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


def scenario_pnl_by_bond(bonds, tenors, par, shape):
    """{bond id: dollar P&L} from fully repricing each bond after shifting the par curve by `shape`."""
    spreads = base_spreads(bonds, tenors, par)
    pts = sorted(shape)
    shifts = [interp(pts, [shape[t] for t in pts], t) for t in tenors]
    shocked = [p + s / 10_000.0 for p, s in zip(par, shifts)]
    df0 = discount_factors(tenors, par)
    df1 = discount_factors(tenors, shocked)
    return {b.id: b.par * (curve_price(b, df1, spreads[b.id]) - curve_price(b, df0, spreads[b.id])) / 100.0
            for b in bonds}


def scenario_pnl(bonds, tenors, par, shape):
    """Dollar P&L from fully repricing every bond after shifting the par curve by `shape`."""
    return sum(scenario_pnl_by_bond(bonds, tenors, par, shape).values())
