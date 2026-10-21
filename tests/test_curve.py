import os
import tempfile
import unittest

from bondshock import Bond, price, load_portfolio
from bondshock.curve import (load_par_curve, interp, discount_factors, bump_par, curve_price,
                             calibrated_spread, base_spreads, key_rate_dv01, scenario_pnl, SCENARIOS)
from bondshock.portfolio import run_shocks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURVE = os.path.join(ROOT, "data", "treasury_par_curve.csv")
SAMPLE = os.path.join(ROOT, "data", "sample_portfolio.csv")
TENORS = [0.5, 1, 2, 3, 5, 7, 10, 20, 30]


def bond(c=0.05, m=10, y=0.05, par=1_000_000, f=2, id="T"):
    return Bond(id, par, c, m, f, y)


class TestBootstrap(unittest.TestCase):
    def test_flat_curve_discount_factors(self):
        df = discount_factors(TENORS, [0.04] * 9)
        for k in (1, 2, 10, 60, 120):
            self.assertAlmostEqual(df[k], (1 + 0.02) ** -k, places=12)

    def test_curve_reproduces_par_bonds(self):
        # A bond whose coupon equals the par yield at a key tenor must price at 100 off that curve
        t, p = load_par_curve(CURVE)
        df = discount_factors(t, p)
        for tenor, y in zip(t, p):
            if tenor >= 1:
                b = bond(c=y, m=int(tenor), y=y)
                self.assertAlmostEqual(curve_price(b, df), 100.0, places=8)

    def test_discount_factors_decrease(self):
        t, p = load_par_curve(CURVE)
        df = discount_factors(t, p)
        self.assertTrue(all(a > b for a, b in zip(df[1:], df[2:])))

    def test_interp_flat_beyond_ends(self):
        self.assertEqual(interp([1, 2], [0.03, 0.05], 0.5), 0.03)
        self.assertEqual(interp([1, 2], [0.03, 0.05], 9), 0.05)
        self.assertAlmostEqual(interp([1, 2], [0.03, 0.05], 1.5), 0.04)


class TestSpread(unittest.TestCase):
    def test_zero_spread_on_flat_curve_when_yield_matches(self):
        df = discount_factors(TENORS, [0.05] * 9)
        self.assertAlmostEqual(calibrated_spread(bond(0.05, 10, 0.05), df), 0.0, places=9)

    def test_spread_reprices_bond_to_its_own_yield(self):
        t, p = load_par_curve(CURVE)
        df = discount_factors(t, p)
        b = bond(0.0625, 7, 0.0594)
        s = calibrated_spread(b, df)
        self.assertAlmostEqual(curve_price(b, df, s), price(b), places=9)

    def test_spread_is_about_the_yield_gap(self):
        # A bond yielding 100bp over its par yield should carry a spread near 100bp
        t, p = load_par_curve(CURVE)
        df = discount_factors(t, p)
        s = calibrated_spread(bond(0.06, 10, p[TENORS.index(10)] + 0.01), df)
        self.assertTrue(0.0095 < s < 0.0105, s)

    def test_rejects_non_semiannual(self):
        t, p = load_par_curve(CURVE)
        df = discount_factors(t, p)
        with self.assertRaises(ValueError):
            calibrated_spread(bond(f=1), df)


class TestBump(unittest.TestCase):
    def test_bump_is_triangular_and_local(self):
        par = [0.04] * 9
        j = TENORS.index(10)
        out = bump_par(TENORS, par, j, 1.0)
        self.assertAlmostEqual(out[j] - 0.04, 0.0001, places=12)
        self.assertAlmostEqual(out[TENORS.index(7)] - 0.04, 0.0, places=12)     # lower neighbour: weight 0
        self.assertAlmostEqual(out[TENORS.index(20)] - 0.04, 0.0, places=12)    # upper neighbour: weight 0
        self.assertEqual(out[0], 0.04)
        self.assertEqual(out[-1], 0.04)

    def test_neighbouring_tenors_get_no_bump(self):
        # Triangular weights fall to 0 at the neighbouring key tenors (5y and 10y for a 7y bump)
        out = bump_par(TENORS, [0.04] * 9, TENORS.index(7), 1.0)
        self.assertAlmostEqual(out[TENORS.index(5)] - 0.04, 0.0, places=12)
        self.assertAlmostEqual(out[TENORS.index(7)] - 0.04, 0.0001, places=12)


class TestRisk(unittest.TestCase):
    def setUp(self):
        self.t, self.p = load_par_curve(CURVE)
        self.bonds = load_portfolio(SAMPLE)

    def test_key_rates_sum_to_parallel_dv01(self):
        total = sum(v for _, v in key_rate_dv01(self.bonds, self.t, self.p))
        parallel = -scenario_pnl(self.bonds, self.t, self.p, {0.5: 1, 30: 1})
        self.assertAlmostEqual(total, parallel, delta=0.01 * parallel)

    def test_dv01_positive_for_long_bonds(self):
        self.assertTrue(all(v > -1e-6 for _, v in key_rate_dv01(self.bonds, self.t, self.p)))

    def test_zero_coupon_risk_peaks_at_its_maturity(self):
        zero = [bond(0.0, 10, 0.0515, id="Z")]
        krd = dict(key_rate_dv01(zero, self.t, self.p))
        self.assertEqual(max(krd, key=krd.get), 10)

    def test_curve_dv01_close_to_yield_dv01(self):
        # Two methods, one number: curve key-rate DV01 total vs mv * modified duration / 10,000
        from bondshock import summary
        total = sum(v for _, v in key_rate_dv01(self.bonds, self.t, self.p))
        self.assertAlmostEqual(total, summary(self.bonds)["dv01"], delta=0.02 * total)

    def test_flat_curve_parallel_matches_yield_shock(self):
        # On a flat par curve, a parallel curve shift is the same as shifting each bond's yield
        flat = [0.05] * 9
        bonds = [bond(0.05, 10, 0.05, id="A"), bond(0.05, 5, 0.05, id="B"), bond(0.05, 30, 0.05, id="C")]
        curve = scenario_pnl(bonds, TENORS, flat, {0.5: 100, 30: 100})
        yld = run_shocks(bonds, [100])[0]["full"]
        self.assertAlmostEqual(curve, yld, places=4)

    def test_scenario_signs(self):
        s = {n: scenario_pnl(self.bonds, self.t, self.p, sh) for n, sh in SCENARIOS.items()}
        self.assertLess(s["Parallel +100bp"], 0)
        self.assertGreater(s["Parallel -100bp"], 0)
        self.assertLess(s["Bear steepener"], 0)
        self.assertGreater(s["Bull flattener"], 0)
        # Positive convexity: the gain from -100bp exceeds the loss from +100bp
        self.assertGreater(s["Parallel -100bp"], -s["Parallel +100bp"])


class TestLoading(unittest.TestCase):
    def _write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def test_rejects_unsorted_tenors(self):
        path = self._write("as_of,tenor_years,par_yield\n2026-01-01,2,0.04\n2026-01-01,1,0.03\n")
        with self.assertRaises(ValueError):
            load_par_curve(path)

    def test_rejects_single_tenor(self):
        path = self._write("as_of,tenor_years,par_yield\n2026-01-01,2,0.04\n")
        with self.assertRaises(ValueError):
            load_par_curve(path)

    def test_loads_repo_curve(self):
        t, p = load_par_curve(CURVE)
        self.assertEqual(t, TENORS)
        self.assertTrue(all(0 < y < 0.15 for y in p))


if __name__ == "__main__":
    unittest.main()
