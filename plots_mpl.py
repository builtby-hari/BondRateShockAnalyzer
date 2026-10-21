#!/usr/bin/env python3
"""Optional matplotlib versions of the main charts, written as PNG into docs/mpl/.

The core project is standard library only. This script needs the extra:
    pip install -r requirements-extra.txt
    python3 plots_mpl.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from bondshock import curve  # noqa: E402
from bondshock.portfolio import load_portfolio, run_shocks  # noqa: E402

PORTFOLIO = os.path.join(ROOT, "data", "sample_portfolio.csv")
CURVE = os.path.join(ROOT, "data", "treasury_par_curve.csv")
NAVY, TEAL, SLATE, GRID, TEXT = "#1f3a5f", "#14b8a6", "#64748b", "#cbd5e1", "#334155"


def make_plots(outdir, portfolio=PORTFOLIO, curve_path=CURVE):
    """Write the five main charts as PNG files. Returns the list of paths written."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    bonds = load_portfolio(portfolio)
    rows = run_shocks(bonds)
    tenors, par = curve.load_par_curve(curve_path)
    krd = curve.key_rate_dv01(bonds, tenors, par)
    scen = {n: curve.scenario_pnl_by_bond(bonds, tenors, par, s) for n, s in curve.SCENARIOS.items()}
    xs = [r["shock_bp"] for r in rows]
    written = []

    def save(fig, name):
        path = os.path.join(outdir, name + ".png")
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
        written.append(path)

    def style(ax, title, xlabel, ylabel):
        ax.set_title(title, color=TEXT, fontweight="bold")
        ax.set_xlabel(xlabel, color=TEXT)
        ax.set_ylabel(ylabel, color=TEXT)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(colors=TEXT)

    # 1. P&L vs parallel shock
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(xs, [r["full"] / 1e6 for r in rows], color=NAVY, lw=2.2, label="Full Repricing")
    ax.plot(xs, [r["duration"] / 1e6 for r in rows], color=SLATE, lw=2.2, ls="--", label="Duration Only")
    ax.plot(xs, [r["dur_convexity"] / 1e6 for r in rows], color=TEAL, lw=2.2, ls=":",
            label="Duration + Convexity")
    style(ax, "Portfolio P&L vs Parallel Yield Shock ($mm)", "Parallel Shock (bp)", "P&L ($mm)")
    ax.axhline(0, color=TEXT, lw=0.8, ls="--")
    ax.legend(frameon=False)
    save(fig, "pnl_vs_shock")

    # 2. Approximation error
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(xs, [r["dur_error"] / 1e6 for r in rows], color=SLATE, lw=2.2, ls="--", label="Duration Only")
    ax.plot(xs, [r["durcvx_error"] / 1e6 for r in rows], color=TEAL, lw=2.2, ls=":",
            label="Duration + Convexity")
    style(ax, "Approximation Error vs Full Repricing ($mm)", "Parallel Shock (bp)", "Error ($mm)")
    ax.axhline(0, color=TEXT, lw=0.8, ls="--")
    ax.legend(frameon=False)
    save(fig, "approx_error")

    # 3. Key-rate DV01 by tenor
    fig, ax = plt.subplots(figsize=(9, 5.2))
    labels = [f"{t:g}y" for t, _ in krd]
    ax.bar(labels, [v for _, v in krd], color=NAVY, width=0.6)
    for i, (_, v) in enumerate(krd):
        ax.text(i, v, f"{v:,.0f}", ha="center", va="bottom", color=TEXT, fontsize=9)
    style(ax, "Key-Rate DV01 by Tenor ($ per +1bp)", "Par-Curve Tenor", "DV01 ($)")
    save(fig, "krd_by_tenor")

    # 4. Par and zero curve
    grid = [k / 2 for k in range(1, 61)]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(grid, [100 * curve.interp(tenors, par, t) for t in grid], color=NAVY, lw=2.2,
            label="Par Yield")
    ax.plot(tenors, [100 * p for p in par], "o", color=NAVY)
    ax.plot([t for t, _ in curve.zero_rates(tenors, par, 60)],
            [100 * z for _, z in curve.zero_rates(tenors, par, 60)], color=TEAL, lw=2.2, ls=":",
            label="Zero Rate (Bootstrapped)")
    style(ax, "US Treasury Par and Zero Curve (%)", "Maturity (Years)", "Yield (%)")
    ax.legend(frameon=False)
    save(fig, "treasury_curve")

    # 5. Scenario x bond heatmap
    names = list(curve.SCENARIOS)
    ids = [b.id for b in bonds]
    grid_vals = [[scen[n][b] / 1e6 for b in ids] for n in names]
    peak = max(abs(v) for row in grid_vals for v in row)
    fig, ax = plt.subplots(figsize=(9, 5.2))
    im = ax.imshow(grid_vals, cmap="RdBu", vmin=-peak, vmax=peak, aspect="auto")
    ax.set_xticks(range(len(ids)), ids, rotation=30, ha="right", color=TEXT)
    ax.set_yticks(range(len(names)), names, color=TEXT)
    for i, row in enumerate(grid_vals):
        for j, v in enumerate(row):
            ax.text(j, i, f"{v:,.2f}", ha="center", va="center", color=TEXT, fontsize=9)
    fig.colorbar(im, ax=ax, label="P&L ($mm)")
    ax.set_title("Non-Parallel Scenario P&L by Bond ($mm)", color=TEXT, fontweight="bold")
    save(fig, "scenario_heatmap")

    return written


def main():
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("matplotlib is not installed. It is an optional extra: "
              "pip install -r requirements-extra.txt", file=sys.stderr)
        return 1
    for path in make_plots(os.path.join(ROOT, "docs", "mpl")):
        print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
