"""Fixed-rate bullet bond pricing and analytic risk measures.

Conventions: the bond is valued on a coupon date (no accrued interest), the
yield is a nominal annual rate compounded `frequency` times a year, and
price is per 100 of par.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Bond:
    id: str
    par: float          # face amount in dollars
    coupon: float       # annual coupon rate, decimal (0.045 = 4.5%)
    maturity: float     # years to maturity (a multiple of 1/frequency)
    frequency: int      # coupons per year
    yld: float          # annual yield, decimal

    def __post_init__(self):
        if self.par <= 0:
            raise ValueError(f"{self.id}: par must be positive")
        if self.frequency < 1:
            raise ValueError(f"{self.id}: frequency must be >= 1")
        n = self.maturity * self.frequency
        if n < 1 or abs(n - round(n)) > 1e-9:
            raise ValueError(f"{self.id}: maturity must be a positive multiple of 1/frequency")

    @property
    def periods(self):
        return int(round(self.maturity * self.frequency))

    def with_yield(self, yld):
        return Bond(self.id, self.par, self.coupon, self.maturity, self.frequency, yld)


def cashflow_times(bond):
    """Return [(period k, time in years, cash flow per 100 par)]."""
    f, n = bond.frequency, bond.periods
    coupon = 100.0 * bond.coupon / f
    return [(k, k / f, coupon + (100.0 if k == n else 0.0)) for k in range(1, n + 1)]


def price(bond, yld=None):
    """Clean = dirty price per 100 par at a coupon date."""
    y = bond.yld if yld is None else yld
    f = bond.frequency
    return sum(cf / (1 + y / f) ** k for k, _, cf in cashflow_times(bond))


def macaulay_duration(bond, yld=None):
    y = bond.yld if yld is None else yld
    f = bond.frequency
    p = price(bond, y)
    return sum(t * cf / (1 + y / f) ** k for k, t, cf in cashflow_times(bond)) / p


def modified_duration(bond, yld=None):
    y = bond.yld if yld is None else yld
    return macaulay_duration(bond, y) / (1 + y / bond.frequency)


def convexity(bond, yld=None):
    """Analytic convexity (1/P) d2P/dy2, in years squared."""
    y = bond.yld if yld is None else yld
    f = bond.frequency
    p = price(bond, y)
    s = sum(cf * k * (k + 1) / (1 + y / f) ** (k + 2) for k, _, cf in cashflow_times(bond))
    return s / (f * f * p)
