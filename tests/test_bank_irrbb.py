import contextlib
import io
import os
import unittest

import cli
from bondshock import curve, irrbb
from bondshock.irrbb import Line

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURVE = os.path.join(ROOT, "data", "treasury_par_curve.csv")
SHEET = os.path.join(ROOT, "data", "sample_balance_sheet.csv")
TENORS, PAR = curve.load_par_curve(CURVE)
UP200 = {"Parallel up": {t: 200.0 for t in curve.IRRBB_TENORS}}


class TestEve(unittest.TestCase):
    def test_fixed_asset_eve_change_is_pv_difference(self):
        asset = Line("loan", "asset", 1000.0, "fixed", 0.05, months=24)
        base, rows = irrbb.eve_table([asset], TENORS, PAR, UP200, tier1=100.0)

        def pv(par):
            df = curve.discount_factors(TENORS, par)
            return sum(25.0 * df[k] for k in (1, 2, 3, 4)) + 1000.0 * df[4]

        shocked = pv([p + 0.02 for p in PAR])
        self.assertAlmostEqual(base, pv(PAR), places=9)
        self.assertAlmostEqual(rows[0][2], shocked - pv(PAR), places=9)
        self.assertAlmostEqual(rows[0][3], 100.0 * (shocked - pv(PAR)) / 100.0, places=9)
        self.assertLess(rows[0][2], 0)

    def test_liability_flips_sign(self):
        a = Line("a", "asset", 1000.0, "fixed", 0.05, months=24)
        l = Line("l", "liability", 1000.0, "fixed", 0.05, months=24)
        _, ra = irrbb.eve_table([a], TENORS, PAR, UP200, 1.0)
        _, rl = irrbb.eve_table([l], TENORS, PAR, UP200, 1.0)
        self.assertAlmostEqual(ra[0][2], -rl[0][2], places=9)

    def test_scenario_wiring_and_outlier_flag(self):
        sc = curve.irrbb_scenarios(*curve.IRRBB_2024["USD"])
        lines = irrbb.load_balance_sheet(SHEET)
        _, rows = irrbb.eve_table(lines, TENORS, PAR, sc, tier1=90e6)
        self.assertEqual([r[0] for r in rows], list(sc))
        self.assertEqual(irrbb.outlier(rows), [])
        _, tight = irrbb.eve_table(lines, TENORS, PAR, sc, tier1=10e6)
        flagged = irrbb.outlier(tight)
        self.assertIn("Parallel up", flagged)
        self.assertNotIn("Parallel down", flagged)  # gains are never flagged

    def test_floor_limits_down_shock(self):
        low = [0.001] * len(PAR)
        down = {"Parallel down": {t: -200.0 for t in curve.IRRBB_TENORS}}
        a = [Line("a", "asset", 1000.0, "fixed", 0.05, months=60)]
        _, raw = irrbb.eve_table(a, TENORS, low, down, 1.0)
        _, flo = irrbb.eve_table(a, TENORS, low, down, 1.0, floor=curve.eba_floor)
        self.assertLess(flo[0][2], raw[0][2])


class TestNii(unittest.TestCase):
    def test_floating_asset_gains_shock_times_time_after_reset(self):
        a = Line("c&i", "asset", 1000.0, "floating", 0.05, months=3)
        base, rows = irrbb.nii_table([a], TENORS, PAR, UP200)
        self.assertAlmostEqual(base, 50.0)
        self.assertAlmostEqual(rows[0][2], 1000.0 * 0.02 * 0.75)

    def test_deposit_beta_zero_vs_one(self):
        for beta, expect in ((0.0, 0.0), (1.0, -1000.0 * 0.02), (0.5, -1000.0 * 0.01)):
            d = Line("dep", "liability", 1000.0, "nmd", 0.01, core=0.8, avg_life=3, beta=beta)
            _, rows = irrbb.nii_table([d], TENORS, PAR, UP200)
            self.assertAlmostEqual(rows[0][2], expect)

    def test_fixed_rolls_at_maturity(self):
        a = Line("f", "asset", 1000.0, "fixed", 0.04, months=6)
        _, rows = irrbb.nii_table([a], TENORS, PAR, UP200)
        self.assertAlmostEqual(rows[0][2], 1000.0 * 0.02 * 0.5)


class TestGapAndCli(unittest.TestCase):
    def test_gap_buckets_and_totals(self):
        lines = [Line("a", "asset", 100.0, "floating", 0.05, months=3),
                 Line("d", "liability", 50.0, "nmd", 0.01, core=0.6, avg_life=2.5, beta=0.3)]
        rows = {r[0]: r for r in irrbb.gap_table(lines)}
        self.assertEqual(rows["3M-6M"][1], 100.0)
        self.assertEqual(rows["O/N"][2], 20.0)
        self.assertEqual(rows["2Y-3Y"][2], 30.0)
        self.assertAlmostEqual(irrbb.gap_table(lines)[-1][4], 50.0)

    def test_bad_input_rejected(self):
        with self.assertRaises(ValueError):
            Line("x", "asset", 1.0, "nmd", 0.0)
        with self.assertRaises(ValueError):
            Line("x", "asset", 1.0, "fixed", 0.05, months=0)

    def test_cli_prints_tables(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--balance-sheet", SHEET, "--tier1", "90000000"])
        out = buf.getvalue()
        for s in ("Repricing gap", "EVE sensitivity", "12-month NII", "Steepener", "Outlier check"):
            self.assertIn(s, out)

    def test_cli_requires_tier1(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["--balance-sheet", SHEET])

    def test_cli_rejects_tier1_in_millions(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            cli.main(["--balance-sheet", SHEET, "--tier1", "90"])
        self.assertIn("in dollars", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
