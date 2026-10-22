"""Bank-level IRRBB: repricing gap, EVE sensitivity and 12-month NII sensitivity.

A balance sheet is a CSV of lines (see data/sample_balance_sheet.csv):

    name, side (asset|liability), balance, rate_type (fixed|floating|nmd), rate_pct,
    months, core_pct, avg_life_years, beta

- fixed: bullet, semiannual coupon at rate_pct, `months` = remaining maturity.
- floating: rate_pct is the current all-in rate, `months` = months to the next reset (and the reset
  interval after it). After a reset the position is worth par.
- nmd: non-maturity deposit paying rate_pct. core_pct of the balance is stable and treated as a bullet at
  avg_life_years; the rest is overnight. beta is the share of a rate shock passed on to the deposit rate.

Shocks come from curve.irrbb_scenarios and the optional floors from curve.floor_shape. EVE discounts off
the bootstrapped par curve (no commercial spread). Simplifications are listed in the README.
"""

import csv

from . import curve

# Standard time buckets (years): (label, start, end). Cash flows sit at the bucket midpoint in the gap table.
BUCKETS = [("O/N", 0.0, 1 / 365), ("O/N-1M", 1 / 365, 1 / 12), ("1M-3M", 1 / 12, 0.25),
           ("3M-6M", 0.25, 0.5), ("6M-9M", 0.5, 0.75), ("9M-12M", 0.75, 1.0), ("1Y-1.5Y", 1.0, 1.5),
           ("1.5Y-2Y", 1.5, 2.0), ("2Y-3Y", 2.0, 3.0), ("3Y-4Y", 3.0, 4.0), ("4Y-5Y", 4.0, 5.0),
           ("5Y-6Y", 5.0, 6.0), ("6Y-7Y", 6.0, 7.0), ("7Y-8Y", 7.0, 8.0), ("8Y-9Y", 8.0, 9.0),
           ("9Y-10Y", 9.0, 10.0), ("10Y-15Y", 10.0, 15.0), ("15Y-20Y", 15.0, 20.0), ("20Y+", 20.0, 1e9)]
OUTLIER_PCT = 15.0   # supervisory outlier test: delta EVE > 15% of Tier 1 (Basel Framework SRP31)
HORIZON = 1.0


class Line:
    def __init__(self, name, side, balance, rate_type, rate, months=0.0, core=0.0, avg_life=0.0, beta=0.0):
        if side not in ("asset", "liability"):
            raise ValueError(f"{name}: side must be asset or liability")
        if rate_type not in ("fixed", "floating", "nmd"):
            raise ValueError(f"{name}: rate_type must be fixed, floating or nmd")
        if balance < 0 or not 0 <= core <= 1 or not 0 <= beta <= 1:
            raise ValueError(f"{name}: need balance >= 0, core_pct and beta within [0, 100%] / [0, 1]")
        if rate_type != "nmd" and months <= 0:
            raise ValueError(f"{name}: months must be positive")
        if rate_type == "nmd" and (side != "liability" or (core > 0 and avg_life <= 0)):
            raise ValueError(f"{name}: nmd must be a liability; core balance needs avg_life_years > 0")
        self.name, self.side, self.balance, self.rate_type = name, side, balance, rate_type
        self.rate, self.months, self.core, self.avg_life, self.beta = rate, months, core, avg_life, beta

    @property
    def sign(self):
        return 1.0 if self.side == "asset" else -1.0

    def repricing(self):
        """[(years to reprice, balance)] used by the gap table."""
        if self.rate_type == "nmd":
            return [(0.0, self.balance * (1 - self.core)), (self.avg_life, self.balance * self.core)]
        return [(self.months / 12.0, self.balance)]

    def cashflows(self):
        """[(years, amount)] for EVE."""
        if self.rate_type == "floating":
            t = self.months / 12.0
            return [(t, self.balance * (1 + self.rate * t))]
        if self.rate_type == "nmd":
            return [(0.0, self.balance * (1 - self.core)), (self.avg_life, self.balance * self.core)]
        mat = self.months / 12.0
        times, t = [], mat
        while t > 1e-9:
            times.append(t)
            t -= 1 / curve.FREQ
        times = sorted(times)
        prev, out = 0.0, []
        for t in times:
            out.append((t, self.balance * self.rate * (t - prev)))
            prev = t
        out[-1] = (mat, out[-1][1] + self.balance)
        return out


def load_balance_sheet(path):
    """Read a balance sheet CSV. Rates in the file are percent; blank optional cells mean 0."""
    need = ("name", "side", "balance", "rate_type", "rate_pct")
    lines = []
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        missing = [c for c in need if c not in (rd.fieldnames or [])]
        if missing:
            raise ValueError(f"balance sheet CSV missing columns: {missing}")
        num = lambda r, k: float(r.get(k) or 0)
        for r in rd:
            lines.append(Line(r["name"], r["side"].strip().lower(), float(r["balance"]),
                              r["rate_type"].strip().lower(), float(r["rate_pct"]) / 100.0,
                              num(r, "months"), num(r, "core_pct") / 100.0, num(r, "avg_life_years"),
                              num(r, "beta")))
    if not lines:
        raise ValueError("balance sheet has no lines")
    return lines


def gap_table(lines):
    """[(label, assets, liabilities, gap, cumulative gap)] over the standard buckets."""
    a, l = [0.0] * len(BUCKETS), [0.0] * len(BUCKETS)
    for ln in lines:
        for t, bal in ln.repricing():
            i = next(i for i, (_, lo, hi) in enumerate(BUCKETS) if t < hi or hi >= 1e9)
            (a if ln.side == "asset" else l)[i] += bal
    rows, cum = [], 0.0
    for (label, _, _), x, y in zip(BUCKETS, a, l):
        cum += x - y
        rows.append((label, x, y, x - y, cum))
    return rows


def shifts(shape, tenors, par, floor=None):
    """Scenario shift in bp at each curve tenor, with the optional floor applied."""
    if floor:
        return curve.floor_shape(shape, tenors, par, floor)
    pts = sorted(shape)
    return {t: curve.interp(pts, [shape[x] for x in pts], t) for t in tenors}


def _df_at(df, t):
    return curve.interp([k / curve.FREQ for k in range(len(df))], df, t)


def eve(lines, df):
    """Economic value of equity: PV of asset cash flows less PV of liability cash flows."""
    return sum(ln.sign * amt * _df_at(df, t) for ln in lines for t, amt in ln.cashflows())


def eve_table(lines, tenors, par, scenarios, tier1, floor=None):
    """(base EVE, [(scenario, shocked EVE, delta EVE, delta as % of Tier 1)])."""
    base = eve(lines, curve.discount_factors(tenors, par))
    rows = []
    for name, shape in scenarios.items():
        sh = shifts(shape, tenors, par, floor)
        df = curve.discount_factors(tenors, [p + sh[t] / 10_000.0 for t, p in zip(tenors, par)])
        e = eve(lines, df)
        rows.append((name, e, e - base, 100.0 * (e - base) / tier1))
    return base, rows


def outlier(rows, threshold_pct=OUTLIER_PCT):
    """Scenarios whose EVE loss exceeds threshold_pct of Tier 1 (a supervisory screen, not advice)."""
    return [r[0] for r in rows if -r[3] > threshold_pct]


def outlier_test(rows, threshold_pct=OUTLIER_PCT):
    """(worst scenario, its delta EVE as % of Tier 1, "PASS" or "BREACH") for the largest EVE loss
    across the scenarios, against threshold_pct of Tier 1 (BCBS d368 paragraph 88)."""
    name, _, _, pct = min(rows, key=lambda r: r[3])
    return name, pct, "BREACH" if -pct > threshold_pct else "PASS"


def nii(lines, shift_bp):
    """12-month net interest income with a constant balance sheet and an instant shock of `shift_bp`.

    Floating lines reset at `months` and every `months` after that; fixed lines roll once at maturity into
    the same coupon plus the shock; NMD rates move by beta x shock from day one.
    """
    s = shift_bp / 10_000.0
    total = 0.0
    for ln in lines:
        if ln.rate_type == "nmd":
            inc = ln.balance * (ln.rate + ln.beta * s) * HORIZON
        elif ln.rate_type == "floating":
            step, inc, t = ln.months / 12.0, 0.0, 0.0
            while t < HORIZON - 1e-12:
                end = min(t + step, HORIZON)
                inc += ln.balance * (ln.rate + (s if t > 0 else 0.0)) * (end - t)
                t = end
        else:
            mat = min(ln.months / 12.0, HORIZON)
            inc = ln.balance * (ln.rate * HORIZON + s * (HORIZON - mat))
        total += ln.sign * inc
    return total


def nii_table(lines, tenors, par, scenarios, floor=None):
    """(base NII, [(scenario, NII, delta NII)]). The shift is the scenario's value at the shortest tenor."""
    base = nii(lines, 0.0)
    rows = []
    for name, shape in scenarios.items():
        n = nii(lines, shifts(shape, tenors, par, floor)[tenors[0]])
        rows.append((name, n, n - base))
    return base, rows
