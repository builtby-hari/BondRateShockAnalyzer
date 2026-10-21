#!/usr/bin/env python3
"""Command-line interface for the bond rate-shock analyzer."""

import argparse
import csv
import os

from bondshock import load_portfolio, run_shocks, summary, KEY_TENORS, SHOCKS_BP
from bondshock import charts
from bondshock import curve

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORTFOLIO = os.path.join(HERE, "data", "sample_portfolio.csv")
DEFAULT_CURVE = os.path.join(HERE, "data", "treasury_par_curve.csv")


def build_parser():
    p = argparse.ArgumentParser(description="Bond portfolio rate-shock analyzer")
    p.add_argument("--portfolio", default=DEFAULT_PORTFOLIO,
                   help="portfolio CSV (id, par, coupon_pct, maturity_yrs, frequency, yield_pct)")
    p.add_argument("--step", type=int, default=50, help="shock step in bp")
    p.add_argument("--max-shock", type=int, default=300, help="largest absolute shock in bp")
    p.add_argument("--csv", help="write the shock table (with bucket attribution) to this CSV")
    p.add_argument("--charts", help="write SVG charts into this directory")
    p.add_argument("--curve", nargs="?", const=DEFAULT_CURVE, metavar="PAR_CURVE_CSV",
                   help="also report curve-based key-rate DV01 and curve scenarios "
                        "(semiannual bonds only; optional par-curve CSV, default data/treasury_par_curve.csv)")
    return p


def print_curve_report(bonds, path):
    tenors, par = curve.load_par_curve(path)
    spreads = curve.base_spreads(bonds, tenors, par)
    print("\nCurve-based risk (par curve bootstrapped to discount factors; each bond carries a constant "
          "spread fixed at its base value)")
    print("Par curve: " + "  ".join(f"{t:g}y {y*100:.2f}%" for t, y in zip(tenors, par)))
    print("Spread over curve (bp): " + "  ".join(f"{b.id} {int(round(spreads[b.id]*1e4)):+d}" for b in bonds))
    krd = curve.key_rate_dv01(bonds, tenors, par)
    print("\nKey-rate DV01 ($ loss for +1bp at each tenor; positive = loses when rates rise)")
    for t, v in krd:
        print(f"  {t:>5g}y {v:>12,.0f}")
    print(f"  {'Total':>6} {sum(v for _, v in krd):>12,.0f}")
    print("\nCurve scenarios (full repricing, portfolio P&L in $)")
    for name, shape in curve.SCENARIOS.items():
        print(f"  {name:<24}{curve.scenario_pnl(bonds, tenors, par, shape):>14,.0f}")


def write_csv(path, rows):
    head = (["shock_bp", "full_pnl", "duration_pnl", "dur_convexity_pnl", "duration_error",
             "dur_convexity_error"] + [f"bucket_{t}y_pnl" for t in KEY_TENORS] +
            ["bucket_sum", "interaction_residual"])
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(head)
        for r in rows:
            w.writerow([r["shock_bp"]] + [f"{r[k]:.2f}" for k in
                       ("full", "duration", "dur_convexity", "dur_error", "durcvx_error")] +
                       [f"{b:.2f}" for b in r["buckets"]] +
                       [f"{r['bucket_sum']:.2f}", f"{r['interaction']:.2f}"])


def main(argv=None):
    a = build_parser().parse_args(argv)
    bonds = load_portfolio(a.portfolio)
    shocks = sorted(set(range(-a.max_shock, a.max_shock + 1, a.step)) | {0})
    rows = run_shocks(bonds, shocks)
    s = summary(bonds)
    print(f"Portfolio: {len(bonds)} bonds  par ${s['par']:,.0f}  MV ${s['market_value']:,.0f}  "
          f"wtd yield {s['wavg_yield']*100:.3f}%")
    print(f"Modified duration {s['mod_duration']:.3f}   Convexity {s['convexity']:.1f}   "
          f"DV01 ${s['dv01']:,.0f}\n")
    print("Parallel shock P&L ($)")
    print(f"{'Shock':>6} {'Full reprice':>14} {'Duration':>14} {'Dur+Cvx':>14} "
          f"{'Dur err':>11} {'D+C err':>11} {'%MV':>7}")
    for r in rows:
        print(f"{r['shock_bp']:>+6d} {r['full']:>14,.0f} {r['duration']:>14,.0f} "
              f"{r['dur_convexity']:>14,.0f} {r['dur_error']:>11,.0f} {r['durcvx_error']:>11,.0f} "
              f"{100*r['full']/s['market_value']:>+7.2f}")
    print("\nYield-bucket attribution ($, each bucket's yield shocked alone by the shock size; "
          "an approximation, see the curve report for true key-rate DV01)")
    print(f"{'Shock':>6} " + " ".join(f"{str(t)+'y':>12}" for t in KEY_TENORS) +
          f" {'Sum':>13} {'Parallel':>13} {'Interact.':>11}")
    for r in rows:
        print(f"{r['shock_bp']:>+6d} " + " ".join(f"{b:>12,.0f}" for b in r["buckets"]) +
              f" {r['bucket_sum']:>13,.0f} {r['full']:>13,.0f} {r['interaction']:>11,.0f}")
    if a.curve:
        print_curve_report(bonds, a.curve)
    if a.csv:
        write_csv(a.csv, rows)
        print(f"\nWrote {a.csv}")
    if a.charts:
        files = charts.write_all(a.charts, bonds, rows, curve_path=a.curve or DEFAULT_CURVE)
        print(f"Wrote {len(files)} SVG charts (light and dark) to {a.charts}")


if __name__ == "__main__":
    main()
