"""Hand-written SVG charts (no plotting library).

Every chart is written twice: a light version (`name.svg`) and a dark version (`name-dark.svg`,
transparent background, for GitHub's dark theme). Regenerate all of them with:

    python3 -m bondshock.charts
"""

import math
import os
import subprocess
import sys
import textwrap
from xml.sax.saxutils import escape

from . import curve
from .portfolio import load_portfolio, run_shocks, KEY_TENORS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PORTFOLIO = os.path.join(ROOT, "data", "sample_portfolio.csv")
DEFAULT_CURVE = os.path.join(ROOT, "data", "treasury_par_curve.csv")

W, H = 760, 440
L, R, T, B = 85, 30, 55, 65
FONT = 'font-family="Helvetica, Arial, sans-serif"'
MONO = 'font-family="Menlo, Consolas, Courier New, monospace"'

THEMES = {
    False: {"navy": "#1f3a5f", "teal": "#14b8a6", "slate": "#64748b", "grid": "#cbd5e1",
            "text": "#334155", "bg": "#ffffff", "neutral": "#f8fafc", "frame": "#cbd5e1"},
    True: {"navy": "#60a5fa", "teal": "#2dd4bf", "slate": "#94a3b8", "grid": "#475569",
           "text": "#e2e8f0", "bg": None, "neutral": "#1e293b", "frame": "#475569"},
}


def _ticks(lo, hi, n=5):
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t, out = math.ceil(lo / step - 1e-9) * step, []
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def _mm(v):
    return f"{v / 1e6:,.1f}"


def _span(vals):
    lo, hi = min(vals + [0]), max(vals + [0])
    pad = (hi - lo) * 0.05 or 1
    t = _ticks(lo - pad, hi + pad)
    step = t[1] - t[0]
    return (t[0] - step if t[0] > lo else t[0]), (t[-1] + step if t[-1] < hi else t[-1])


def _mix(a, b, t):
    """Blend two #rrggbb colours: a at t=0, b at t=1."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


class Chart:
    def __init__(self, th, title, xlabel, ylabel, xmin, xmax, ymin, ymax, xticks=None,
                 xfmt="{:g}", yfmt=_mm):
        self.th = th
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax
        title, xlabel, ylabel = escape(title), escape(xlabel), escape(ylabel)
        txt, grid = th["text"], th["grid"]
        self.p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
                  f'role="img" aria-label="{title}">']
        if th["bg"]:
            self.p.append(f'<rect width="{W}" height="{H}" fill="{th["bg"]}"/>')
        self.p.append(f'<text x="{W/2}" y="28" text-anchor="middle" font-size="16" font-weight="bold" '
                      f'fill="{txt}" {FONT}>{title}</text>')
        for v in _ticks(ymin, ymax):
            y = self.y(v)
            self.p.append(f'<line x1="{L}" x2="{W-R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{grid}"/>')
            self.p.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="{txt}" '
                          f'{FONT}>{yfmt(v)}</text>')
        if ymin < 0 < ymax:
            self.p.append(f'<line x1="{L}" x2="{W-R}" y1="{self.y(0):.1f}" y2="{self.y(0):.1f}" '
                          f'stroke="{txt}" stroke-dasharray="4 3"/>')
        for v in (xticks if xticks is not None else _ticks(xmin, xmax)):
            x = self.x(v)
            self.p.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{H-B}" y2="{H-B+5}" stroke="{txt}"/>')
            self.p.append(f'<text x="{x:.1f}" y="{H-B+19}" text-anchor="middle" font-size="11" fill="{txt}" '
                          f'{FONT}>{xfmt.format(v)}</text>')
        self.p.append(f'<line x1="{L}" x2="{L}" y1="{T}" y2="{H-B}" stroke="{txt}"/>')
        self.p.append(f'<line x1="{L}" x2="{W-R}" y1="{H-B}" y2="{H-B}" stroke="{txt}"/>')
        self.p.append(f'<text x="{(L+W-R)/2}" y="{H-18}" text-anchor="middle" font-size="12" fill="{txt}" '
                      f'{FONT}>{xlabel}</text>')
        self.p.append(f'<text transform="translate(18 {(T+H-B)/2}) rotate(-90)" text-anchor="middle" '
                      f'font-size="12" fill="{txt}" {FONT}>{ylabel}</text>')

    def x(self, v):
        return L + (v - self.xmin) / (self.xmax - self.xmin) * (W - L - R)

    def y(self, v):
        return H - B - (v - self.ymin) / (self.ymax - self.ymin) * (H - T - B)

    def line(self, xs, ys, color, dash=None):
        pts = " ".join(f"{self.x(a):.1f},{self.y(b):.1f}" for a, b in zip(xs, ys))
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.p.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.2"{d}/>')

    def dot(self, a, b, color):
        self.p.append(f'<circle cx="{self.x(a):.1f}" cy="{self.y(b):.1f}" r="3.4" fill="{color}"/>')

    def legend(self, items, x=None, y=None):
        x = L + 14 if x is None else x
        y = T + 8 if y is None else y
        for i, (label, color, dash) in enumerate(items):
            yy = y + i * 18
            d = f' stroke-dasharray="{dash}"' if dash else ""
            self.p.append(f'<line x1="{x}" x2="{x+14}" y1="{yy+3}" y2="{yy+3}" stroke="{color}" '
                          f'stroke-width="3"{d}/>')
            self.p.append(f'<text x="{x+20}" y="{yy+7}" font-size="12" fill="{self.th["text"]}" '
                          f'{FONT}>{escape(label)}</text>')

    def svg(self):
        return "\n".join(self.p + ["</svg>"]) + "\n"


def pnl_chart(rows, dark=False):
    th = THEMES[dark]
    xs = [r["shock_bp"] for r in rows]
    series = [("Full Repricing", "full", th["navy"], None),
              ("Duration Only", "duration", th["slate"], "7 4"),
              ("Duration + Convexity", "dur_convexity", th["teal"], "2 3")]
    lo, hi = _span([r[k] for r in rows for _, k, _, _ in series])
    c = Chart(th, "Portfolio P&L vs Parallel Yield Shock ($mm)", "Parallel Shock (bp)", "P&L ($mm)",
              xs[0], xs[-1], lo, hi, xticks=xs[::2], xfmt="{:+g}")
    for _, k, color, dash in series:
        c.line(xs, [r[k] for r in rows], color, dash)
    c.legend([(s[0], s[2], s[3]) for s in series], y=H - B - 70)
    return c.svg()


def error_chart(rows, dark=False):
    th = THEMES[dark]
    xs = [r["shock_bp"] for r in rows]
    lo, hi = _span([r[k] for r in rows for k in ("dur_error", "durcvx_error")])
    c = Chart(th, "Approximation Error vs Full Repricing ($mm)", "Parallel Shock (bp)", "Error ($mm)",
              xs[0], xs[-1], lo, hi, xticks=xs[::2], xfmt="{:+g}", yfmt=lambda v: f"{v/1e6:,.2f}")
    c.line(xs, [r["dur_error"] for r in rows], th["slate"], "7 4")
    c.line(xs, [r["durcvx_error"] for r in rows], th["teal"], "2 3")
    c.legend([("Duration Only", th["slate"], "7 4"), ("Duration + Convexity", th["teal"], "2 3")],
             x=W - R - 180, y=H - B - 50)
    return c.svg()


def attribution_chart(rows, shocks=(-100, 100), tenors=KEY_TENORS, dark=False):
    th = THEMES[dark]
    by = {r["shock_bp"]: r for r in rows}
    cats = [f"{t}y" for t in tenors] + ["Sum of Buckets", "Parallel (Full)"]
    cols = {shocks[0]: th["teal"], shocks[1]: th["navy"]}
    vals = {s: by[s]["buckets"] + [by[s]["bucket_sum"], by[s]["full"]] for s in shocks}
    lo, hi = _span([v for s in shocks for v in vals[s]])
    c = Chart(th, "Key-Rate Bucket P&L Attribution ($mm)", "Bucket", "P&L ($mm)", 0, len(cats), lo, hi,
              xticks=[])
    gw = (W - L - R) / len(cats)
    bw = gw * 0.32
    for i, cat in enumerate(cats):
        cx = L + gw * (i + 0.5)
        c.p.append(f'<text x="{cx:.1f}" y="{H-B+19}" text-anchor="middle" font-size="11" fill="{th["text"]}" '
                   f'{FONT}>{cat}</text>')
        for j, s in enumerate(shocks):
            v = vals[s][i]
            x0 = cx - bw - 2 if j == 0 else cx + 2
            y0, y1 = sorted((c.y(0), c.y(v)))
            c.p.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1-y0, 0.5):.1f}" '
                       f'fill="{cols[s]}"/>')
    c.legend([(f"{s:+d}bp Shock", cols[s], None) for s in shocks], x=W - R - 150, y=32)
    return c.svg()


def krd_chart(krd, dark=False):
    """Key-rate DV01 by tenor: krd is [(tenor in years, dollar DV01)]."""
    th = THEMES[dark]
    cats = [f"{t:g}y" for t, _ in krd]
    vals = [v for _, v in krd]
    c = Chart(th, "Key-Rate DV01 by Tenor ($ per +1bp)", "Par-Curve Tenor", "DV01 ($)",
              0, len(cats), 0, max(vals) * 1.12, xticks=[], yfmt=lambda v: f"{v:,.0f}")
    gw = (W - L - R) / len(cats)
    bw = gw * 0.6
    for i, (cat, v) in enumerate(zip(cats, vals)):
        cx = L + gw * (i + 0.5)
        y0, y1 = sorted((c.y(0), c.y(v)))
        c.p.append(f'<rect x="{cx-bw/2:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1-y0, 0.5):.1f}" '
                   f'fill="{th["navy"]}"/>')
        c.p.append(f'<text x="{cx:.1f}" y="{y0-6:.1f}" text-anchor="middle" font-size="11" fill="{th["text"]}" '
                   f'{FONT}>{v:,.0f}</text>')
        c.p.append(f'<text x="{cx:.1f}" y="{H-B+19}" text-anchor="middle" font-size="11" fill="{th["text"]}" '
                   f'{FONT}>{cat}</text>')
    return c.svg()


def curve_chart(tenors, par, dark=False):
    """Par yields (dots at the input tenors, line between them) and bootstrapped zero rates."""
    th = THEMES[dark]
    grid = [k / curve.FREQ for k in range(1, 61)]
    par_pct = [100 * curve.interp(tenors, par, t) for t in grid]
    zero_pct = [100 * z for _, z in curve.zero_rates(tenors, par, 60)]
    vals = par_pct + zero_pct
    lo, hi = math.floor(min(vals) * 10) / 10 - 0.1, math.ceil(max(vals) * 10) / 10 + 0.1
    c = Chart(th, "US Treasury Par and Zero Curve (%)", "Maturity (Years)", "Yield (%)",
              0, grid[-1], lo, hi, xticks=[0, 5, 10, 15, 20, 25, 30], yfmt=lambda v: f"{v:.1f}%")
    c.line(grid, par_pct, th["navy"])
    c.line(grid, zero_pct, th["teal"], "7 4")
    for t, p in zip(tenors, par):
        c.dot(t, 100 * p, th["navy"])
    c.legend([("Par Yield (Dots at Curve Tenors)", th["navy"], None),
              ("Zero Rate, Bootstrapped", th["teal"], "7 4")])
    return c.svg()


def heatmap_chart(scenarios, bond_ids, values, dark=False):
    """Scenario x bond P&L grid. values[i][j] is the dollar P&L of bond j under scenario i."""
    th = THEMES[dark]
    left, top, cell_h = 185, 70, 56
    cw = (W - left - 25) / len(bond_ids)
    peak = max(abs(v) for row in values for v in row) or 1.0
    txt = th["text"]
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'role="img" aria-label="Scenario P&amp;L Heatmap ($mm)">']
    if th["bg"]:
        p.append(f'<rect width="{W}" height="{H}" fill="{th["bg"]}"/>')
    p.append(f'<text x="{W/2}" y="28" text-anchor="middle" font-size="16" font-weight="bold" fill="{txt}" '
             f'{FONT}>Non-Parallel Scenario P&amp;L by Bond ($mm)</text>')
    for j, bid in enumerate(bond_ids):
        x = left + cw * (j + 0.5)
        p.append(f'<text x="{x:.1f}" y="{top-10}" text-anchor="middle" font-size="10" fill="{txt}" '
                 f'{FONT}>{escape(bid)}</text>')
    for i, (name, row) in enumerate(zip(scenarios, values)):
        y = top + i * cell_h
        p.append(f'<text x="{left-10}" y="{y + cell_h/2 + 4:.1f}" text-anchor="end" font-size="12" '
                 f'fill="{txt}" {FONT}>{escape(name)}</text>')
        for j, v in enumerate(row):
            t = abs(v) / peak
            end = th["navy"] if v < 0 else th["teal"]
            fill = _mix(th["neutral"], end, t)
            r, g, b = (int(fill[k:k + 2], 16) for k in (1, 3, 5))
            ink = "#ffffff" if 0.299 * r + 0.587 * g + 0.114 * b < 150 else "#0f172a"
            x = left + cw * j
            p.append(f'<rect x="{x+2:.1f}" y="{y+2}" width="{cw-4:.1f}" height="{cell_h-4}" rx="4" fill="{fill}"/>')
            mm = round(v / 1e6, 2) + 0.0  # + 0.0 turns -0.0 into 0.0
            p.append(f'<text x="{x+cw/2:.1f}" y="{y + cell_h/2 + 4:.1f}" text-anchor="middle" font-size="12" '
                     f'fill="{ink}" {FONT}>{mm:,.2f}</text>')
    ly = top + len(scenarios) * cell_h + 22
    p.append(f'<rect x="{left}" y="{ly}" width="14" height="14" rx="3" fill="{th["navy"]}"/>')
    p.append(f'<text x="{left+20}" y="{ly+11}" font-size="12" fill="{txt}" {FONT}>Loss</text>')
    p.append(f'<rect x="{left+80}" y="{ly}" width="14" height="14" rx="3" fill="{th["teal"]}"/>')
    p.append(f'<text x="{left+100}" y="{ly+11}" font-size="12" fill="{txt}" {FONT}>Gain</text>')
    p.append(f'<text x="{left+160}" y="{ly+11}" font-size="12" fill="{txt}" {FONT}>Darker cells show larger '
             f'moves. Each bond is fully repriced at its base spread.</text>')
    p.append("</svg>")
    return "\n".join(p) + "\n"


def banner_svg(dark=False):
    th = THEMES[dark]
    p = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 240" width="1200" height="240" '
         'role="img" aria-label="Bond Rate Shock Analyzer banner">']
    if th["bg"]:
        p.append(f'<rect width="1200" height="240" fill="{th["bg"]}"/>')
    p.append(f'<text x="60" y="108" font-size="52" font-weight="bold" letter-spacing="3" fill="{th["navy"]}" '
             f'{FONT}>Bond Rate Shock Analyzer</text>')
    p.append(f'<text x="60" y="156" font-size="24" fill="{th["text"]}" {FONT}>'
             'Bond Portfolio P&amp;L Under Rate Shocks, with Key-Rate DV01</text>')
    p.append(f'<rect x="60" y="186" width="1080" height="1.5" fill="{th["teal"]}" opacity="0.5"/>')
    p.append(f'<rect x="60" y="184" width="120" height="4" fill="{th["teal"]}"/>')
    p.append("</svg>")
    return "\n".join(p) + "\n"


def terminal_svg(lines, dark=False):
    """Monospace terminal window with three dots. Lines starting with '$ ' are the command."""
    th = THEMES[dark]
    fg = th["text"]
    wrapped = []
    for line in lines:  # wrap long lines the way a terminal would, so nothing is clipped
        wrapped += textwrap.wrap(line, 118, subsequent_indent="    ", break_on_hyphens=False) or [""]
    lines = wrapped
    lh, pad, bar, width, size = 15, 16, 34, 1000, 11
    height = bar + pad * 2 + lh * len(lines) + 4
    window = "#f8fafc" if not dark else "#0f172a"
    titlebar = "#e2e8f0" if not dark else "#1e293b"
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
         f'height="{height}" role="img" aria-label="Sample Terminal Output">',
         f'<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="10" fill="{window}" '
         f'stroke="{th["frame"]}"/>',
         f'<path d="M1 11 a10 10 0 0 1 10 -10 h{width-22} a10 10 0 0 1 10 10 v{bar-11} h-{width-2} z" '
         f'fill="{titlebar}"/>']
    for i, color in enumerate((th["navy"], th["slate"], th["teal"])):
        p.append(f'<circle cx="{22 + i * 20}" cy="{bar/2 + 0.5}" r="6" fill="{color}"/>')
    for i, line in enumerate(lines):
        color = th["teal"] if line.startswith("$ ") else fg
        y = bar + pad + lh * (i + 1)
        p.append(f'<text x="24" y="{y}" font-size="{size}" fill="{color}" {MONO} xml:space="preserve" '
                 f'style="white-space:pre">{escape(line)}</text>')
    p.append("</svg>")
    return "\n".join(p) + "\n"


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def sample_output_text():
    """Real stdout of `python3 cli.py --curve`."""
    cli = os.path.join(ROOT, "cli.py")
    out = subprocess.run([sys.executable, cli, "--curve"], capture_output=True, text=True, check=True)
    return out.stdout


def write_all(outdir, bonds, rows, sample_text=None, curve_path=DEFAULT_CURVE):
    """Write every chart in light and dark. Returns the list of files written."""
    os.makedirs(outdir, exist_ok=True)
    tenors, par = curve.load_par_curve(curve_path)
    krd = curve.key_rate_dv01(bonds, tenors, par)
    scen = {name: curve.scenario_pnl_by_bond(bonds, tenors, par, shape)
            for name, shape in curve.SCENARIOS.items()}
    heat = [[scen[name][b.id] for b in bonds] for name in curve.SCENARIOS]
    written = []
    for dark in (False, True):
        sfx = "-dark" if dark else ""
        charts = {
            "pnl_vs_shock": pnl_chart(rows, dark),
            "approx_error": error_chart(rows, dark),
            "attribution": attribution_chart(rows, dark=dark),
            "krd_by_tenor": krd_chart(krd, dark),
            "treasury_curve": curve_chart(tenors, par, dark),
            "scenario_heatmap": heatmap_chart(list(curve.SCENARIOS), [b.id for b in bonds], heat, dark),
            "banner": banner_svg(dark),
        }
        if sample_text is not None:
            charts["sample-output"] = terminal_svg(["$ python3 cli.py --curve"] + sample_text.splitlines(), dark)
        for name, text in charts.items():
            written.append(_write(os.path.join(outdir, f"{name}{sfx}.svg"), text))
    return written


def main():
    bonds = load_portfolio(DEFAULT_PORTFOLIO)
    rows = run_shocks(bonds)
    out = os.path.join(ROOT, "docs")
    for path in write_all(out, bonds, rows, sample_text=sample_output_text()):
        print("wrote", path)


if __name__ == "__main__":
    main()
