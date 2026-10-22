#!/usr/bin/env python3
"""Command-line interface for the bond rate-shock analyzer."""

import argparse
import csv
import os

from bondshock import load_portfolio, run_shocks, summary, KEY_TENORS, SHOCKS_BP
from bondshock import charts
from bondshock import curve, irrbb

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
    p.add_argument("--irrbb", action="store_true",
                   help="with --curve, also report the six Basel IRRBB shock scenarios (USD calibration)")
    p.add_argument("--floor", action="store_true",
                   help="with --irrbb, hold shocked rates at or above the EBA 2018 floor")
    p.add_argument("--balance-sheet", metavar="CSV",
                   help="bank IRRBB mode: repricing gap, EVE and NII tables for this balance sheet "
                        "(uses --curve's file if given; needs --tier1)")
    p.add_argument("--tier1", type=float, help="Tier 1 capital in dollars, for delta EVE as %% of Tier 1")
    p.add_argument("--currency", default="USD", choices=sorted(curve.IRRBB_2024),
                   help="Basel 2024 shock calibration for the bank scenarios (default USD)")
    p.add_argument("--outlier-pct", type=float, default=irrbb.OUTLIER_PCT,
                   help="outlier test threshold, percent of Tier 1 (default 15)")
    return p


def print_bank_report(a):
    lines = irrbb.load_balance_sheet(a.balance_sheet)
    tenors, par = curve.load_par_curve(a.curve or DEFAULT_CURVE)
    scen = curve.irrbb_scenarios(*curve.IRRBB_2024[a.currency])
    floor = curve.eba_floor if a.floor else None
    assets = sum(l.balance for l in lines if l.side == "asset")
    liabs = sum(l.balance for l in lines if l.side == "liability")
    if a.tier1 < 0.001 * assets:  # real banks hold far more; this is almost always millions typed as dollars
        raise SystemExit(f"error: Tier 1 ${a.tier1:,.0f} is under 0.1% of assets ${assets:,.0f}; "
                         "--tier1 is in dollars (90 million is 90000000)")
    print(f"Bank IRRBB: assets ${assets:,.0f}  liabilities ${liabs:,.0f}  Tier 1 ${a.tier1:,.0f}")
    print(f"Scenarios: Basel 2024 {a.currency} " + ("with EBA floors" if floor else "no floors"))
    print("\nRepricing gap ($; non-empty buckets)")
    print(f"{'Bucket':<9} {'Assets':>14} {'Liabilities':>14} {'Gap':>14} {'Cumulative':>14}")
    for label, x, y, g, cum in irrbb.gap_table(lines):
        if x or y:
            print(f"{label:<9} {x:>14,.0f} {y:>14,.0f} {g:>14,.0f} {cum:>14,.0f}")
    base, rows = irrbb.eve_table(lines, tenors, par, scen, a.tier1, floor)
    print(f"\nEVE sensitivity (base EVE ${base:,.0f})")
    print(f"{'Scenario':<18} {'EVE':>14} {'Delta EVE':>14} {'% Tier 1':>9}")
    for name, e, d, pct in rows:
        print(f"{name:<18} {e:>14,.0f} {d:>14,.0f} {pct:>+9.2f}")
    hit = irrbb.outlier(rows, a.outlier_pct)
    print(f"Outlier check (delta EVE loss > {a.outlier_pct:g}% of Tier 1): "
          + (", ".join(hit) if hit else "none breach") + "  (a screen, not advice)")
    nbase, nrows = irrbb.nii_table(lines, tenors, par, scen, floor)
    print(f"\n12-month NII sensitivity (constant balance sheet, base NII ${nbase:,.0f})")
    print(f"{'Scenario':<18} {'NII':>14} {'Delta NII':>14} {'% Base':>8}")
    for name, n, d in nrows:
        print(f"{name:<18} {n:>14,.0f} {d:>14,.0f} {100*d/nbase:>+8.2f}")
    if a.charts:
        files = charts.write_eve_charts(a.charts, rows)
        print(f"\nWrote {len(files)} SVG charts (light and dark) to {a.charts}")


def print_curve_report(bonds, path, irrbb=False, floor=False):
    tenors, par = curve.load_par_curve(path)
    spreads = curve.base_spreads(bonds, tenors, par)
    print("\nCurve-based risk (par curve bootstrapped to discount factors; each bond carries a constant "
          "spread fixed at its base value)")
    print("Par curve: " + "  ".join(f"{t:g}y {y*100:.2f}%" for t, y in zip(tenors, par)))
    print("Spread over curve (bp): " + "  ".join(f"{b.id} {int(round(s*1e4)):+d}" for b, s in zip(bonds, spreads)))
    krd = curve.key_rate_dv01(bonds, tenors, par)
    print("\nKey-rate DV01 ($ loss for +1bp at each tenor; positive = loses when rates rise)")
    for t, v in krd:
        print(f"  {t:>5g}y {v:>12,.0f}")
    print(f"  {'Total':>6} {sum(v for _, v in krd):>12,.0f}")
    print("\nCurve scenarios (full repricing, portfolio P&L in $)")
    for name, shape in curve.SCENARIOS.items():
        print(f"  {name:<24}{curve.scenario_pnl(bonds, tenors, par, shape):>14,.0f}")
    d, c = curve.effective_duration_convexity(bonds, tenors, par)
    print(f"\nEffective duration {d:.3f}   effective convexity {c:.1f}  (full repricing off the curve, +/-10bp)")
    if irrbb:
        print("\nBasel IRRBB scenarios (USD: parallel 200bp, short 300bp, long 150bp; BCBS d368, "
              + ("EBA floors on)" if floor else "no floors)"))
        for name, shape in curve.irrbb_scenarios().items():
            note = ""
            if floor:
                floored = curve.floor_shape(shape, tenors, par)
                raw = {t: curve.interp(sorted(shape), [shape[x] for x in sorted(shape)], t) for t in tenors}
                if any(abs(floored[t] - raw[t]) > 1e-9 for t in tenors):
                    note = "  (floor applied: shocked rates held at the floor)"
                shape = floored
            print(f"  {name:<24}{curve.scenario_pnl(bonds, tenors, par, shape):>14,.0f}{note}")


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
    p = build_parser()
    a = p.parse_args(argv)
    if a.step <= 0 or a.max_shock <= 0:
        p.error("--step and --max-shock must be positive")
    if a.balance_sheet:
        if not a.tier1 or a.tier1 <= 0:
            p.error("--balance-sheet needs a positive --tier1")
        try:
            print_bank_report(a)
        except (OSError, ValueError, KeyError) as e:
            p.error(str(e))
        return
    if a.irrbb and not a.curve:
        p.error("--irrbb needs --curve")
    if a.floor and not a.irrbb:
        p.error("--floor needs --irrbb")
    try:
        bonds = load_portfolio(a.portfolio)
        if a.curve:
            # Validate the curve path before printing anything, so a bad input fails cleanly.
            tenors, par = curve.load_par_curve(a.curve)
            curve.base_spreads(bonds, tenors, par)
    except (OSError, ValueError, KeyError) as e:
        p.error(str(e))
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
        print_curve_report(bonds, a.curve, a.irrbb, a.floor)
    if a.csv:
        write_csv(a.csv, rows)
        print(f"\nWrote {a.csv}")
    if a.charts:
        files = charts.write_all(a.charts, bonds, rows, curve_path=a.curve or DEFAULT_CURVE)
        print(f"Wrote {len(files)} SVG charts (light and dark) to {a.charts}")


if __name__ == "__main__":
    main()
