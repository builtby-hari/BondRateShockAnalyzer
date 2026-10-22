"""Portfolio loading, shock scenarios and key-rate-style attribution."""

import csv

from .pricing import Bond, price, modified_duration, convexity

SHOCKS_BP = list(range(-300, 301, 50))
KEY_TENORS = (2, 5, 10, 30)


def load_portfolio(path):
    """Read a portfolio CSV: id, par, coupon_pct, maturity_yrs, frequency, yield_pct."""
    bonds = []
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        need = {"id", "par", "coupon_pct", "maturity_yrs", "frequency", "yield_pct"}
        missing = need - set(rd.fieldnames or [])
        if missing:
            raise ValueError(f"portfolio CSV missing columns: {sorted(missing)}")
        for row in rd:
            bonds.append(Bond(row["id"].strip(), float(row["par"]),
                              float(row["coupon_pct"]) / 100, float(row["maturity_yrs"]),
                              int(row["frequency"]), float(row["yield_pct"]) / 100))
    if not bonds:
        raise ValueError("portfolio CSV has no bonds")
    seen = set()
    for b in bonds:
        if b.id in seen:
            raise ValueError(f"portfolio CSV has duplicate bond id {b.id!r}")
        seen.add(b.id)
    return bonds


def key_rate_weights(maturity, tenors=KEY_TENORS):
    """Triangular key-rate weights for a maturity (one per key tenor).

    Weight is 1 at a key tenor and falls linearly to 0 at the neighbouring
    tenors; maturities inside the first or last tenor load 100% on it. The
    weights always sum to 1, so shocking every bucket by the same amount
    equals a parallel shock.
    """
    w = [0.0] * len(tenors)
    if maturity <= tenors[0]:
        w[0] = 1.0
    elif maturity >= tenors[-1]:
        w[-1] = 1.0
    else:
        for i in range(len(tenors) - 1):
            lo, hi = tenors[i], tenors[i + 1]
            if lo <= maturity <= hi:
                w[i + 1] = (maturity - lo) / (hi - lo)
                w[i] = 1.0 - w[i + 1]
                break
    return w


def market_value(bond, yld=None):
    return bond.par * price(bond, yld) / 100.0


def portfolio_mv(bonds):
    return sum(market_value(b) for b in bonds)


def _parallel(bond, bp):
    return bond.yld + bp / 10_000.0


def run_shocks(bonds, shocks=SHOCKS_BP, tenors=KEY_TENORS):
    """Return one dict per shock with dollar P&L by method and by key-rate bucket."""
    base = {b.id: market_value(b) for b in bonds}
    weights = {b.id: key_rate_weights(b.maturity, tenors) for b in bonds}
    rows = []
    for bp in shocks:
        dy = bp / 10_000.0
        full = dur = durcvx = 0.0
        buckets = [0.0] * len(tenors)
        for b in bonds:
            mv = base[b.id]
            full += market_value(b, _parallel(b, bp)) - mv
            d, c = modified_duration(b), convexity(b)
            dur += -d * mv * dy
            durcvx += mv * (-d * dy + 0.5 * c * dy * dy)
            for i, wi in enumerate(weights[b.id]):
                if wi:
                    buckets[i] += market_value(b, b.yld + wi * dy) - mv
        rows.append({"shock_bp": bp, "full": full, "duration": dur, "dur_convexity": durcvx,
                     "dur_error": dur - full, "durcvx_error": durcvx - full,
                     "buckets": buckets, "bucket_sum": sum(buckets),
                     "interaction": full - sum(buckets)})
    return rows


def summary(bonds):
    if not bonds:
        raise ValueError("no bonds")
    mv = portfolio_mv(bonds)
    dur = sum(market_value(b) * modified_duration(b) for b in bonds) / mv
    cvx = sum(market_value(b) * convexity(b) for b in bonds) / mv
    wavg = sum(market_value(b) * b.yld for b in bonds) / mv
    return {"market_value": mv, "par": sum(b.par for b in bonds), "mod_duration": dur,
            "convexity": cvx, "wavg_yield": wavg, "dv01": mv * dur / 10_000.0}
