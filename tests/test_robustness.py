import contextlib
import io
import os
import tempfile
import unittest

import cli
import fetch_curve
from bondshock import Bond, load_portfolio, summary
from bondshock.curve import (base_spreads, discount_factors, effective_duration_convexity,
                             key_rate_dv01, load_par_curve, scenario_pnl, scenario_pnl_by_bond,
                             SCENARIOS, curve_price, calibrated_spread)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURVE = os.path.join(ROOT, "data", "treasury_par_curve.csv")
TENORS = [0.5, 1, 2, 3, 5, 7, 10, 20, 30]
HEADER = "id,par,coupon_pct,maturity_yrs,frequency,yield_pct\n"


def write_portfolio(rows):
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    fh.write(HEADER + "".join(rows))
    fh.close()
    return fh.name


def write_curve(rows):
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    fh.write(rows)
    fh.close()
    return fh.name


def run_cli_out(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        cli.main(argv)
    return out.getvalue()


def run_cli(argv):
    """Run cli.main quietly. Returns (exit code or None, stderr text)."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(argv)
        except SystemExit as e:
            code = e.code
    return code, err.getvalue()


class TestCurveColumnsAndFloorNote(unittest.TestCase):
    def test_missing_curve_column_is_value_error(self):
        path = write_curve("as_of,tenor_years,yield\n2026-01-01,1,0.04\n")
        try:
            with self.assertRaisesRegex(ValueError, "par_yield"):
                load_par_curve(path)
        finally:
            os.remove(path)

    def test_floor_note_only_when_floor_binds(self):
        low = write_curve("as_of,tenor_years,par_yield\n" + "".join(
            f"2026-01-01,{t},0.001\n" for t in (0.5, 1, 2, 5, 10, 30)))
        try:
            self.assertIn("floor applied", run_cli_out(["--curve", low, "--irrbb", "--floor"]))
            self.assertNotIn("floor applied", run_cli_out(["--curve", low, "--irrbb"]))
            self.assertNotIn("floor applied", run_cli_out(["--curve", "--irrbb", "--floor"]))
        finally:
            os.remove(low)

    def test_floor_needs_irrbb(self):
        code, err = run_cli(["--curve", "--floor"])
        self.assertEqual(code, 2)
        self.assertIn("--floor needs --irrbb", err)


class TestDuplicateIds(unittest.TestCase):
    def setUp(self):
        self.t, self.p = load_par_curve(CURVE)
        self.a = Bond("X", 1e6, 0.0475, 2, 2, 0.0477)    # Treasury-like, spread about 0
        self.b = Bond("X", 1e6, 0.0575, 10, 2, 0.0653)   # corporate-like, spread about +150bp

    def test_load_portfolio_rejects_duplicate_ids(self):
        path = write_portfolio(["X,1000000,4.75,2,2,4.77\n", "X,1000000,5.75,10,2,6.53\n"])
        try:
            with self.assertRaisesRegex(ValueError, "duplicate bond id"):
                load_portfolio(path)
        finally:
            os.remove(path)

    def test_spreads_are_per_bond_not_per_id(self):
        alone = base_spreads([self.a], self.t, self.p)[0]
        both = base_spreads([self.a, self.b], self.t, self.p)
        self.assertAlmostEqual(both[0], alone, places=9)
        self.assertGreater(both[1], 0.01)

    def test_key_rates_add_up_with_duplicate_ids(self):
        both = key_rate_dv01([self.a, self.b], self.t, self.p)
        only_a = key_rate_dv01([self.a], self.t, self.p)
        only_b = key_rate_dv01([self.b], self.t, self.p)
        for (_, x), (_, y), (_, z) in zip(both, only_a, only_b):
            self.assertAlmostEqual(x, y + z, delta=1e-6 * max(1.0, abs(x)))

    def test_scenario_pnl_adds_up_with_duplicate_ids(self):
        shape = SCENARIOS["Bear steepener"]
        total = scenario_pnl([self.a, self.b], self.t, self.p, shape)
        parts = scenario_pnl([self.a], self.t, self.p, shape) + scenario_pnl([self.b], self.t, self.p, shape)
        self.assertAlmostEqual(total, parts, delta=1e-6 * abs(total))
        # The by-id dict keeps one entry per id, so it is documented as collapsing duplicates.
        self.assertEqual(len(scenario_pnl_by_bond([self.a, self.b], self.t, self.p, shape)), 1)


class TestEmptyInput(unittest.TestCase):
    def test_summary_rejects_no_bonds(self):
        with self.assertRaisesRegex(ValueError, "no bonds"):
            summary([])

    def test_effective_duration_rejects_no_bonds(self):
        t, p = load_par_curve(CURVE)
        with self.assertRaisesRegex(ValueError, "no bonds"):
            effective_duration_convexity([], t, p)


class TestShortAndNegativeRates(unittest.TestCase):
    def test_half_year_bond_on_curve(self):
        t, p = load_par_curve(CURVE)
        b = Bond("S", 1e6, 0.04, 0.5, 2, 0.045)
        spread = calibrated_spread(b, discount_factors(t, p))
        self.assertTrue(-0.05 < spread < 0.05)
        krd = key_rate_dv01([b], t, p)
        self.assertGreater(sum(v for _, v in krd), 0)

    def test_negative_rate_curve_discount_factors_and_spread(self):
        neg = [-0.005] * len(TENORS)
        df = discount_factors(TENORS, neg)
        self.assertTrue(all(df[k] < df[k + 1] for k in range(1, len(df) - 1)))
        self.assertTrue(all(x > 1.0 for x in df[1:]))
        b = Bond("N", 1e6, -0.005, 5, 2, -0.005)
        self.assertAlmostEqual(calibrated_spread(b, df), 0.0, places=8)
        self.assertAlmostEqual(curve_price(b, df, 0.0), 100.0, delta=1e-6)


class TestFetchParse(unittest.TestCase):
    def test_parses_fred_csv_and_skips_missing_values(self):
        text = "observation_date,DGS2\n2026-10-16,4.77\n2026-10-19,.\n2026-10-20,4.80\n"
        self.assertEqual(fetch_curve.parse(text), {"2026-10-16": 4.77, "2026-10-20": 4.80})


class TestCliErrors(unittest.TestCase):
    def test_step_must_be_positive(self):
        code, err = run_cli(["--step", "0"])
        self.assertEqual(code, 2)
        self.assertIn("must be positive", err)

    def test_max_shock_must_be_positive(self):
        code, err = run_cli(["--max-shock", "-5"])
        self.assertEqual(code, 2)
        self.assertIn("must be positive", err)

    def test_irrbb_needs_curve(self):
        code, err = run_cli(["--irrbb"])
        self.assertEqual(code, 2)
        self.assertIn("--irrbb needs --curve", err)

    def test_quarterly_bond_in_curve_mode_is_a_clean_error(self):
        path = write_portfolio(["Q,1000000,5,5,4,5\n"])
        try:
            code, err = run_cli(["--portfolio", path, "--curve"])
        finally:
            os.remove(path)
        self.assertEqual(code, 2)
        self.assertIn("semiannual", err)

    def test_missing_portfolio_is_a_clean_error(self):
        code, err = run_cli(["--portfolio", os.path.join(ROOT, "data", "does_not_exist.csv")])
        self.assertEqual(code, 2)
        self.assertIn("does_not_exist", err)

    def test_curve_report_runs_end_to_end(self):
        code, _ = run_cli(["--curve", "--irrbb"])
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
