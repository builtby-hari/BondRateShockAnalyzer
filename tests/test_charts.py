import importlib.util
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from bondshock import charts, curve
from bondshock.portfolio import load_portfolio, run_shocks
from bondshock.pricing import Bond, price, cashflow_times

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "data", "sample_portfolio.csv")
CURVE = os.path.join(ROOT, "data", "treasury_par_curve.csv")
CHART_NAMES = ("pnl_vs_shock", "approx_error", "attribution", "krd_by_tenor",
               "treasury_curve", "scenario_heatmap", "banner", "sample-output")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestCurveHelpers(unittest.TestCase):
    def setUp(self):
        self.tenors, self.par = curve.load_par_curve(CURVE)

    def test_zero_rate_at_half_year_equals_par(self):
        zeros = curve.zero_rates(self.tenors, self.par)
        self.assertAlmostEqual(zeros[0][0], 0.5)
        self.assertAlmostEqual(zeros[0][1], self.par[0], places=12)

    def test_par_bond_prices_to_par_off_zero_rates(self):
        zeros = dict(curve.zero_rates(self.tenors, self.par))
        y2 = self.par[self.tenors.index(2)]
        bond = Bond("T", 1.0, y2, 2, 2, y2)  # 2y bond whose coupon equals its par yield
        pv = sum(cf * (1 + zeros[t] / 2) ** (-2 * t) for _, t, cf in cashflow_times(bond))
        self.assertAlmostEqual(pv, 100.0, places=6)
        self.assertAlmostEqual(price(bond), 100.0, places=6)

    def test_per_bond_scenario_pnl_sums_to_portfolio(self):
        bonds = load_portfolio(SAMPLE)
        for shape in curve.SCENARIOS.values():
            by_bond = curve.scenario_pnl_by_bond(bonds, self.tenors, self.par, shape)
            self.assertEqual(set(by_bond), {b.id for b in bonds})
            self.assertAlmostEqual(sum(by_bond.values()),
                                   curve.scenario_pnl(bonds, self.tenors, self.par, shape), places=4)


class TestCharts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bonds = load_portfolio(SAMPLE)
        cls.rows = run_shocks(cls.bonds)
        cls.dir = tempfile.mkdtemp()
        cls.files = charts.write_all(cls.dir, cls.bonds, cls.rows,
                                     sample_text="line one <&>\nline two", curve_path=CURVE)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_every_chart_has_light_and_dark_file(self):
        names = {os.path.basename(f) for f in self.files}
        for base in CHART_NAMES:
            self.assertIn(base + ".svg", names)
            self.assertIn(base + "-dark.svg", names)
        self.assertEqual(len(names), 2 * len(CHART_NAMES))

    def test_every_svg_is_valid_xml(self):
        for f in self.files:
            root = ET.parse(f).getroot()
            self.assertTrue(root.tag.endswith("svg"), f)

    def test_dark_versions_are_transparent_and_differ(self):
        for f in self.files:
            if not f.endswith("-dark.svg"):
                continue
            dark = _read(f)
            light = _read(f[:-len("-dark.svg")] + ".svg")
            self.assertNotIn('fill="#ffffff"/>', dark)  # no opaque background rect
            self.assertNotEqual(dark, light)

    def test_sample_output_is_escaped(self):
        text = _read(os.path.join(self.dir, "sample-output.svg"))
        self.assertIn("line one &lt;&amp;&gt;", text)
        ET.fromstring(text)

    def test_heatmap_has_one_cell_per_scenario_and_bond(self):
        text = _read(os.path.join(self.dir, "scenario_heatmap.svg"))
        root = ET.fromstring(text)
        ns = "{http://www.w3.org/2000/svg}"
        cells = [r for r in root.iter(ns + "rect") if r.get("rx") == "4"]
        self.assertEqual(len(cells), len(curve.SCENARIOS) * len(self.bonds))


@unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib not installed (optional extra)")
class TestMatplotlibPlots(unittest.TestCase):
    def test_pngs_are_written(self):
        import plots_mpl
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        files = plots_mpl.make_plots(d)
        self.assertEqual(len(files), 5)
        for f in files:
            with open(f, "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
