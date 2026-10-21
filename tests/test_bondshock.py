import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from bondshock import (Bond, price, modified_duration, convexity, macaulay_duration,
                       key_rate_weights, run_shocks, load_portfolio, summary)
from bondshock.portfolio import market_value, KEY_TENORS
from bondshock import charts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "data", "sample_portfolio.csv")


def bond(c=0.05, m=10, f=2, y=0.05, par=1_000_000):
    return Bond("T", par, c, m, f, y)


class TestPricing(unittest.TestCase):
    def test_par_when_yield_equals_coupon(self):
        for f in (1, 2, 4, 12):
            for m in (1, 5, 10, 30):
                self.assertAlmostEqual(price(bond(0.045, m, f, 0.045)), 100.0, places=9)

    def test_premium_and_discount(self):
        self.assertGreater(price(bond(0.06, y=0.04)), 100)
        self.assertLess(price(bond(0.03, y=0.05)), 100)

    def test_zero_coupon_closed_form(self):
        self.assertAlmostEqual(price(bond(0.0, 5, 2, 0.04)), 100 / 1.02 ** 10, places=9)

    def test_modified_duration_matches_finite_difference(self):
        for b in (bond(0.05, 2), bond(0.03, 10, y=0.06), bond(0.07, 30, 1, 0.04)):
            h = 1e-5
            fd = -(price(b, b.yld + h) - price(b, b.yld - h)) / (2 * h * price(b))
            self.assertAlmostEqual(modified_duration(b), fd, places=6)

    def test_convexity_matches_finite_difference(self):
        for b in (bond(0.05, 2), bond(0.03, 10, y=0.06), bond(0.07, 30, 1, 0.04)):
            h = 1e-4
            fd = (price(b, b.yld + h) + price(b, b.yld - h) - 2 * price(b)) / (h * h * price(b))
            self.assertAlmostEqual(convexity(b), fd, delta=1e-3 * fd)

    def test_macaulay_of_zero_is_maturity(self):
        self.assertAlmostEqual(macaulay_duration(bond(0.0, 7, 2, 0.05)), 7.0, places=9)

    def test_validation(self):
        with self.assertRaises(ValueError):
            Bond("x", 100, 0.05, 2.3, 2, 0.05)
        with self.assertRaises(ValueError):
            Bond("x", -1, 0.05, 2, 2, 0.05)


class TestWeights(unittest.TestCase):
    def test_weights_sum_to_one(self):
        for m in (0.5, 2, 3, 5, 7.5, 10, 20, 30, 40):
            self.assertAlmostEqual(sum(key_rate_weights(m)), 1.0)

    def test_known_points(self):
        self.assertEqual(key_rate_weights(5), [0, 1, 0, 0])
        self.assertEqual(key_rate_weights(1), [1, 0, 0, 0])
        self.assertEqual(key_rate_weights(50), [0, 0, 0, 1])
        w = key_rate_weights(3.5)
        self.assertAlmostEqual(w[0], 0.5)
        self.assertAlmostEqual(w[1], 0.5)


class TestShocks(unittest.TestCase):
    def setUp(self):
        self.bonds = load_portfolio(SAMPLE)
        self.rows = {r["shock_bp"]: r for r in run_shocks(self.bonds, list(range(-300, 301, 50)) + [-10, 10, 1])}

    def test_zero_shock_is_zero(self):
        r = run_shocks(self.bonds, [0])[0]
        self.assertAlmostEqual(r["full"], 0, places=6)
        self.assertAlmostEqual(r["dur_convexity"], 0, places=6)

    def test_rates_up_prices_down(self):
        self.assertLess(self.rows[100]["full"], 0)
        self.assertGreater(self.rows[-100]["full"], 0)

    def test_convexity_improves_large_shocks(self):
        for bp in (-300, -200, -100, 100, 200, 300):
            r = self.rows[bp]
            self.assertLess(abs(r["durcvx_error"]), abs(r["dur_error"]), bp)
        # duration alone always understates the gain and overstates the loss
        self.assertLess(self.rows[300]["duration"], self.rows[300]["full"])
        self.assertLess(self.rows[-300]["duration"], self.rows[-300]["full"])

    def test_small_shock_duration_is_accurate(self):
        r = self.rows[1]
        self.assertAlmostEqual(r["duration"], r["full"], delta=abs(r["full"]) * 0.01)

    def test_buckets_sum_to_parallel_for_small_shocks(self):
        for bp in (-10, 10):
            r = self.rows[bp]
            self.assertAlmostEqual(r["bucket_sum"], r["full"], delta=abs(r["full"]) * 0.01)

    def test_bucket_sum_equals_parallel_when_exact(self):
        # a single bond with a 5y maturity loads fully on the 5y bucket, so the sum is exact
        b = [bond(0.04, 5, 2, 0.045)]
        r = run_shocks(b, [100])[0]
        self.assertAlmostEqual(r["bucket_sum"], r["full"], places=6)
        self.assertAlmostEqual(r["buckets"][1], r["full"], places=6)

    def test_portfolio_dv01_matches_one_bp_reprice(self):
        s = summary(self.bonds)
        up = run_shocks(self.bonds, [1])[0]["full"]
        self.assertAlmostEqual(-up, s["dv01"], delta=s["dv01"] * 0.01)

    def test_pnl_is_sum_of_bond_pnl(self):
        bp = 50
        direct = sum(market_value(b, b.yld + bp / 1e4) - market_value(b) for b in self.bonds)
        self.assertAlmostEqual(self.rows[bp]["full"], direct, places=6)


class TestIO(unittest.TestCase):
    def test_sample_loads(self):
        self.assertEqual(len(load_portfolio(SAMPLE)), 7)

    def test_bad_columns(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("id,par\nA,100\n")
        try:
            with self.assertRaises(ValueError):
                load_portfolio(fh.name)
        finally:
            os.unlink(fh.name)

    def test_charts_are_valid_svg(self):
        rows = run_shocks(load_portfolio(SAMPLE))
        for fn in (charts.pnl_chart, charts.error_chart, charts.attribution_chart):
            s = fn(rows)
            self.assertTrue(s.startswith("<svg") and s.rstrip().endswith("</svg>"))
            ET.fromstring(s)  # well-formed XML (e.g. ampersands escaped)


if __name__ == "__main__":
    unittest.main()
