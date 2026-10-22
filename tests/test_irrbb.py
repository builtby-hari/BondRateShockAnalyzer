import math
import os
import unittest

from bondshock import Bond, price, macaulay_duration, modified_duration, convexity, load_portfolio, summary
from bondshock.curve import (load_par_curve, irrbb_scenarios, effective_duration_convexity,
                             scenario_pnl, IRRBB_USD, IRRBB_2024, eba_floor, floor_shape)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURVE = os.path.join(ROOT, "data", "treasury_par_curve.csv")
SAMPLE = os.path.join(ROOT, "data", "sample_portfolio.csv")


class TestTextbookValues(unittest.TestCase):
    def test_ten_year_six_percent_par_bond(self):
        # Classic textbook case (Fabozzi): 10y 6% semiannual at 6% -> Macaulay 7.66, modified 7.44
        b = Bond("T", 1_000_000, 0.06, 10, 2, 0.06)
        self.assertAlmostEqual(price(b), 100.0, places=9)
        self.assertAlmostEqual(macaulay_duration(b), 7.66, places=2)
        self.assertAlmostEqual(modified_duration(b), 7.44, places=2)

    def test_zero_coupon_closed_forms(self):
        # Zero: Macaulay = T, convexity = n(n+1)/(f^2 (1+y/f)^2) with n = f*T
        b = Bond("Z", 1_000_000, 0.0, 20, 2, 0.05)
        self.assertAlmostEqual(macaulay_duration(b), 20.0, places=9)
        self.assertAlmostEqual(convexity(b), 40 * 41 / (4 * 1.025 ** 2), places=6)


class TestIrrbb(unittest.TestCase):
    def test_six_scenarios_with_basel_formulas(self):
        sc = irrbb_scenarios()
        self.assertEqual(len(sc), 6)
        self.assertEqual(set(sc["Parallel up"].values()), {200.0})
        short = 300 * math.exp(-2 / 4)
        long_ = 150 * (1 - math.exp(-2 / 4))
        self.assertAlmostEqual(sc["Short rates up"][2], short)
        self.assertAlmostEqual(sc["Steepener"][2], -0.65 * short + 0.9 * long_)
        self.assertAlmostEqual(sc["Flattener"][2], 0.8 * short - 0.6 * long_)

    def test_steepener_and_flattener_shapes(self):
        sc = irrbb_scenarios()
        self.assertLess(sc["Steepener"][0.5], 0)
        self.assertGreater(sc["Steepener"][30], 0)
        self.assertGreater(sc["Flattener"][0.5], 0)
        self.assertLess(sc["Flattener"][30], 0)

    def test_pnl_signs_on_sample(self):
        t, p = load_par_curve(CURVE)
        bonds = load_portfolio(SAMPLE)
        pnl = {n: scenario_pnl(bonds, t, p, s) for n, s in irrbb_scenarios().items()}
        self.assertLess(pnl["Parallel up"], 0)
        self.assertGreater(pnl["Parallel down"], 0)
        self.assertLess(pnl["Short rates up"], 0)

    def test_bis_worked_example(self):
        # BCBS d368 Annex 2 examples: t = 3.5y, short = long = 100bp -> scalar 0.417, steepener +25.4, flattener -1.6
        sc = irrbb_scenarios(100, 100, 100, tenors=(3.5,))
        self.assertAlmostEqual(sc["Short rates up"][3.5], 41.7, places=1)
        self.assertAlmostEqual(sc["Steepener"][3.5], 25.4, places=1)
        self.assertAlmostEqual(sc["Flattener"][3.5], -1.6, places=1)

    def test_rotations_use_absolute_shock_sizes(self):
        # The rotation formulas take |short| and |long|, so a negative input size cannot flip them.
        self.assertEqual(irrbb_scenarios(-200, -300, -150)["Steepener"], irrbb_scenarios()["Steepener"])
        self.assertEqual(irrbb_scenarios(-200, -300, -150)["Flattener"], irrbb_scenarios()["Flattener"])


class TestIrrbbSources(unittest.TestCase):
    def test_usd_2016_calibration(self):
        # BCBS d368 (2016) Annex 2, USD: parallel 200, short 300, long 150; x = 4.
        self.assertEqual(IRRBB_USD, {"parallel_bp": 200, "short_bp": 300, "long_bp": 150})

    def test_2024_recalibration_table(self):
        # BCBS d578 (July 2024, in force 2026-01-01), SRP31.90 Table 2 in the BIS annex:
        # (parallel, short, long); every value a multiple of 25bp.
        self.assertEqual(IRRBB_2024["USD"], (200, 300, 225))
        self.assertEqual(IRRBB_2024["EUR"], (225, 350, 200))
        self.assertEqual(IRRBB_2024["JPY"], (100, 100, 100))
        for vals in IRRBB_2024.values():
            self.assertTrue(all(v % 25 == 0 for v in vals))

    def test_2024_usd_long_shock_at_30y(self):
        sc = irrbb_scenarios(*IRRBB_2024["USD"])
        long30 = 225 * (1 - math.exp(-30 / 4))
        short30 = 300 * math.exp(-30 / 4)
        self.assertAlmostEqual(sc["Steepener"][30], -0.65 * short30 + 0.9 * long30)

    def test_floor_binds_only_when_rate_is_low(self):
        self.assertAlmostEqual(eba_floor(0), -0.01)
        self.assertAlmostEqual(eba_floor(10), -0.005)
        self.assertEqual(eba_floor(25), 0.0)
        t, par = [1, 10], [0.005, 0.04]
        out = floor_shape({1: -200, 10: -200}, t, par, floor=lambda x: 0.0)
        self.assertAlmostEqual(out[1], -50.0)    # 0.5% rate floored at 0 -> only -50bp
        self.assertAlmostEqual(out[10], -200.0)  # 4% rate - 2% stays above floor
        neg = floor_shape({1: -200, 10: -200}, t, [-0.02, 0.04], floor=lambda x: 0.0)
        self.assertAlmostEqual(neg[1], 0.0)      # already below floor: no further fall, no lift


class TestEffectiveDuration(unittest.TestCase):
    def test_flat_curve_matches_analytic(self):
        t = [0.5, 1, 2, 5, 10, 30]
        b = Bond("A", 1_000_000, 0.05, 10, 2, 0.05)
        d, c = effective_duration_convexity([b], t, [0.05] * 6)
        self.assertAlmostEqual(d, modified_duration(b), delta=0.005)
        self.assertAlmostEqual(c, convexity(b), delta=0.5)

    def test_sample_close_to_analytic(self):
        t, p = load_par_curve(CURVE)
        bonds = load_portfolio(SAMPLE)
        d, c = effective_duration_convexity(bonds, t, p)
        self.assertAlmostEqual(d, summary(bonds)["mod_duration"], delta=0.05 * d)
        self.assertGreater(c, 0)


if __name__ == "__main__":
    unittest.main()
