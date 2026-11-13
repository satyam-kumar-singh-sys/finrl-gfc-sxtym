"""Build a one-file public dashboard: docs/index.html
===================================================

WHY A SINGLE FILE
-----------------
The competition scores transparency and documentation, and a live page is the
cheapest way to show both. But a website that depends on a server, a database or
a CDN can break, and a broken link is worse than no link. So this writes ONE
self-contained HTML file:

    * all styling inline (no external stylesheet);
    * the chart embedded as a base64 PNG (no image hosting);
    * every number read from the committed logs (no API calls at view time).

That means it works from a USB stick, from GitHub Pages, or inside a sandboxed
preview — and it can never show a number that disagrees with the trade log,
because it is built from that log.

GitHub Pages (once, in repo Settings → Pages → Deploy from branch → main → /docs)
will serve it at https://<user>.github.io/<repo>/ and refresh it every time the
daily-report workflow commits a new version.

Usage
-----
    python -m scripts.dashboard
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs"
FIGS = ROOT / "figs"
TD = 252


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _img_b64(path: Path) -> Optional[str]:
    """Embed a PNG as a data URI so the HTML needs no external files."""
    if not path.exists():
        return None
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def _read(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        return df if not df.empty else None
    except Exception:
        return None


def _metrics(equity: pd.Series) -> Dict[str, float]:
    """Same definitions as scripts/report.py, with the small-sample guard."""
    r = equity.pct_change().dropna()
    out: Dict[str, float] = {
        "sessions": int(len(r)),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) > 1 else 0.0,
        "max_dd": float((equity / equity.cummax() - 1).min()) if len(equity) > 1 else 0.0,
    }
    if len(r) >= 20:
        vol = float(r.std(ddof=1) * TD ** 0.5)
        out["sharpe"] = float((r.mean() * TD - 0.02) / vol) if vol > 0 else float("nan")
        out["vol"] = vol
    return out


def _table(df: pd.DataFrame, columns: List[str], limit: int = 12) -> str:
    """Render the last `limit` rows of a DataFrame as a simple HTML table."""
    if df is None or df.empty:
        return '<p class="muted">Nothing recorded yet.</p>'
    cols = [c for c in columns if c in df.columns]
    head = "".join(f"<th>{c}</th>" for c in cols)
    rows = []
    for _, row in df.tail(limit).iloc[::-1].iterrows():
        cells = "".join(f"<td>{_fmt(row[c])}</td>" for c in cols)
        rows.append(f"<tr>{cells}</tr>")
    return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def _fmt(value) -> str:
    """Format one cell. Timestamps are the tricky case.

    pandas parses a mixed column of ISO strings into Timestamps, and printing one
    of those gives "2026-11-13 00:00:00" — which the table then glued to the
    remaining text as "2026-11-13 00:00:00T14:45:31+00:00". Ugly on a page the
    judges read, so timestamps get an explicit, unambiguous rendering.
    """
    if isinstance(value, float):
        return f"{value:,.4f}"
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build() -> Path:
    equity, decisions, trades = _read(RESULTS / "daily_equity.csv"), _read(LOGS / "decisions.csv"), _read(LOGS / "trades.csv")
    health = None
    if (RESULTS / "health.json").exists():
        health = json.loads((RESULTS / "health.json").read_text())
    target = None
    if (RESULTS / "last_target.json").exists():
        target = json.loads((RESULTS / "last_target.json").read_text())
    elif decisions is not None and not decisions.empty and "target" in decisions.columns:
        # No rebalance has been recorded yet (e.g. the first runs were dry runs),
        # but the intent is still in the decision log — show it rather than an
        # empty box. last_target.json remains the authoritative record.
        try:
            target = {"as_of": str(decisions.iloc[-1].get("signal_date", "")),
                      "target": json.loads(decisions.iloc[-1]["target"]),
                      "source": "latest decision log row"}
        except (ValueError, TypeError):
            target = None
    summary = (RESULTS / "live_summary.md").read_text() if (RESULTS / "live_summary.md").exists() else ""
    bt = None
    if (RESULTS / "backtest_metrics.json").exists():
        bt = json.loads((RESULTS / "backtest_metrics.json").read_text())

    eq_series = None
    if equity is not None and "equity" in equity.columns:
        col = "date_ny" if "date_ny" in equity.columns else equity.columns[0]
        eq_series = equity.assign(date=pd.to_datetime(equity[col])).groupby("date")["equity"].last().sort_index()

    m = _metrics(eq_series) if eq_series is not None and len(eq_series) > 1 else {}
    live_png, bt_png = _img_b64(FIGS / "live_equity.png"), _img_b64(FIGS / "ata_equity.png")

    # ---- headline cards ---------------------------------------------------
    cards = []
    if eq_series is not None and len(eq_series):
        cards.append(("Equity", f"${eq_series.iloc[-1]:,.0f}"))
        cards.append(("Total return", f"{m.get('total_return', 0) * 100:+.2f}%"))
        cards.append(("Max drawdown", f"{m.get('max_dd', 0) * 100:.2f}%"))
        if "sharpe" in m:
            cards.append(("Sharpe", f"{m['sharpe']:.2f}"))
    cards.append(("Sessions logged", str(m.get("sessions", 0))))
    if health:
        cards.append(("Health", "OK" if health.get("ok") else "CHECK"))
    card_html = "".join(
        f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in cards
    )

    # ---- holdings ---------------------------------------------------------
    holdings = (
        f'<p class="muted">No target recorded yet.</p>' if not target
        else "<table><thead><tr><th>symbol</th><th>target weight</th></tr></thead><tbody>"
        + "".join(f"<tr><td>{s}</td><td>{w:.2%}</td></tr>"
                  for s, w in sorted(target.get("target", {}).items(), key=lambda kv: -kv[1]))
        + "</tbody></table>"
        + f'<p class="muted">As of signal date {target.get("as_of", "?")} · written {target.get("written", "?")}</p>'
    )

    health_html = '<p class="muted">No health report yet.</p>'
    if health:
        health_html = "<ul>" + "".join(
            f'<li>{"✅" if c["ok"] else "❌"} <b>{c["name"]}</b> — {c["detail"]}</li>'
            for c in health.get("checks", [])
        ) + "</ul>"

    bt_html = '<p class="muted">No backtest file.</p>'
    if bt:
        mrows = bt.get("metrics", {})
        net = mrows.get("ATA v1.0 (net)") or mrows.get("RDA (v1.0)") or {}
        if net:
            bt_html = (
                f"<p>Sample {bt.get('sample', ['?', '?'])[0]} → {bt.get('sample', ['?', '?'])[1]}, "
                f"net of {bt.get('cost_bps', 10):.0f} bp costs:</p><ul>"
                f"<li>Sharpe <b>{net.get('sharpe', float('nan')):.2f}</b></li>"
                f"<li>Sortino <b>{net.get('sortino', float('nan')):.2f}</b></li>"
                f"<li>Max drawdown <b>{net.get('max_dd', 0) * 100:.1f}%</b></li>"
                f"<li>CAGR <b>{net.get('cagr', 0) * 100:.1f}%</b></li></ul>"
            )

    live_chart = (f'<img alt="live equity curve" src="{live_png}">' if live_png
                  else '<p class="muted">Live chart appears after the first few sessions.</p>')
    bt_chart = f'<img alt="backtest equity curve" src="{bt_png}">' if bt_png else ""

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ATA v1.0 — live paper-trading dashboard</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 2rem 1.25rem 4rem; line-height: 1.5; color: #111; background: #fff; }}
  @media (prefers-color-scheme: dark) {{ body {{ color:#e8e8e8; background:#0d1117; }} .card {{ background:#161b22 !important; border-color:#30363d !important; }} table {{ border-color:#30363d; }} th, td {{ border-color:#30363d !important; }} code {{ background:#161b22; }} }}
  main {{ max-width: 980px; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 .25rem; }}
  h2 {{ font-size: 1.05rem; margin: 2rem 0 .6rem; border-bottom: 1px solid #8883; padding-bottom: .3rem; }}
  .sub {{ color:#666; margin: 0 0 1.5rem; font-size: .9rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: .75rem; }}
  .card {{ border: 1px solid #ddd; border-radius: 10px; padding: .85rem 1rem; background:#fafafa; }}
  .card .k {{ font-size: .72rem; letter-spacing: .04em; text-transform: uppercase; color:#666; }}
  .card .v {{ font-size: 1.35rem; font-weight: 600; margin-top: .15rem; }}
  img {{ max-width: 100%; height: auto; border-radius: 8px; margin-top: .5rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .86rem; margin-top: .5rem; }}
  th, td {{ border-bottom: 1px solid #e5e5e5; text-align: left; padding: .35rem .5rem; }}
  th {{ font-weight: 600; color:#555; }}
  .muted {{ color:#777; font-size: .85rem; }}
  ul {{ padding-left: 1.1rem; }}
  .note {{ border-left: 3px solid #bbb; padding: .5rem .9rem; color:#555; font-size: .85rem; background:#8881; }}
  footer {{ margin-top: 3rem; color:#777; font-size: .8rem; }}
</style></head>
<body><main>
  <h1>Adaptive Trend Allocation v1.0</h1>
  <p class="sub">AI4Finance Open Trading Challenge · FinRL-X framework · Alpaca <b>paper</b> account ·
     built {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>
  <p class="note">This page is generated from the same committed trade log the judges can read
     (<code>logs/</code>, <code>results/</code>). If a number here ever disagreed with those files,
     the files would be right.</p>

  <div class="cards">{card_html}</div>

  <h2>Live equity curve</h2>
  {live_chart}

  <h2>Current target</h2>
  {holdings}

  <h2>Bot health</h2>
  {health_html}

  <h2>Recent decisions</h2>
  {_table(decisions, ["time_utc", "signal_date", "action", "max_drift", "risk_state", "target"], 10)}

  <h2>Recent orders</h2>
  {_table(trades, ["time_utc", "mode", "symbol", "side", "qty", "status"], 10)}

  <h2>Backtest (2007–2026)</h2>
  {bt_html}
  {bt_chart}

  <h2>Latest summary</h2>
  <pre style="white-space:pre-wrap;font-size:.85rem">{summary}</pre>

  <footer>
    Research code, paper trading only — not investment advice. Framework © AI4Finance Foundation
    (Apache-2.0); strategy and runner original to this submission.
  </footer>
</main></body></html>
"""
    DOCS.mkdir(exist_ok=True)
    out = DOCS / "index.html"
    out.write_text(html)
    print(f"dashboard → {out} ({len(html)/1024:.0f} KB, self-contained)")
    return out


if __name__ == "__main__":
    build()
