"""Research harness — step 1: compare strategy families honestly.

This is the exploratory record behind STRATEGY.md §2, kept so the rejected
candidates can be re-run. It is **not** imported by the production code path;
it shares only the metrics function with ``scripts/backtest.py``.

Every variant: signal on close of t, exposure from t+lag, costs on turnover.
Rebalance frequency is explicit (daily / weekly / monthly) because that is what
drives turnover and therefore net performance.

Run:  python -m scripts.research_sector_momentum
"""
import numpy as np
import pandas as pd

from scripts.backtest import metrics
from scripts.data import load_prices, load_vix

TD = 252


def load_panel(symbols, start="2007-06-01"):
    """{"close": wide panel, "vix": series} for the research helpers below."""
    syms = [s for s in symbols if s != "^VIX"]
    return {"close": load_prices(syms, start=start), "vix": load_vix(start=start)}


def get_data(symbols, start="2007-06-01"):
    d = load_panel(symbols, start)
    return d["close"], d["vix"]


def build(C):
    P = np.asarray(C, dtype=float)
    R = np.zeros_like(P)
    R[1:] = P[1:] / P[:-1] - 1.0
    return np.nan_to_num(R, nan=0.0)


def rebal_mask(dates, freq):
    d = pd.Series(dates, index=dates)
    if freq == "D":
        return np.ones(len(dates), bool)
    if freq == "W":
        return (d.dt.day_of_week == 4).values
    if freq == "M":
        return (d.dt.to_period("M") != d.dt.to_period("M").shift(1)).values
    raise ValueError(freq)


def simulate(C, Wtarget, dates, freq="W", lag=1, cost_bps=10.0, override=None):
    """Commit Wtarget only on rebalance dates (from close of previous session)."""
    mask = rebal_mask(dates, freq)
    if override is not None:
        mask = mask | override
    Wc = np.zeros_like(Wtarget)
    last = np.zeros(Wtarget.shape[1])
    for t in range(len(dates)):
        if mask[t]:
            last = Wtarget[t]
        Wc[t] = last
    Wl = np.vstack([np.zeros((lag, Wtarget.shape[1])), Wc[:-lag]])
    R = build(C)
    gross = (Wl * R).sum(axis=1)
    turn = np.abs(np.diff(Wc, axis=0, prepend=np.zeros((1, Wtarget.shape[1])))).sum(axis=1)
    net = gross - turn * cost_bps / 1e4
    return pd.DataFrame({"gross": gross, "ret": net, "turn": turn,
                         "inv": Wc.sum(axis=1)}, index=dates)


def report(name, sim, extra=""):
    m, g = metrics(sim["ret"]), metrics(sim["gross"])
    print(f"{name:<40} NET sh {m['sharpe']:5.2f} cagr {m['cagr']*100:5.1f}% dd {m['max_dd']*100:6.1f}% "
          f"sortino {m['sortino']:5.2f} | GROSS sh {g['sharpe']:5.2f} | turn {sim['turn'].mean()*100:5.1f}%/d {extra}", flush=True)
    return m


# --------------------------------------------------------------------------- #
# Target builders
# --------------------------------------------------------------------------- #
def tgt_ma_timing(C, sym="SPY", ma=200, safe="IEF", risk_frac=1.0):
    ok = (C[sym] > C[sym].rolling(ma).mean()).values
    W = np.zeros_like(C.values)
    i, j = list(C.columns).index(sym), list(C.columns).index(safe)
    W[:, i] = np.where(ok, risk_frac, 0.0)
    W[:, j] = np.where(ok, 1 - risk_frac, 1.0)
    return W


def tgt_topk_mom(C, dates, universe, k=3, mom=126, skip=5, safe=None, cash="BIL", ma=None):
    cols = list(C.columns)
    S = (C.shift(skip) / C.shift(skip + mom) - 1)
    if ma:
        S = S.where(C > C.rolling(ma).mean())
    idx = [cols.index(s) for s in universe if s in cols]
    W = np.zeros_like(C.values)
    for t in range(len(dates)):
        row = S.iloc[t, idx].dropna()
        row = row[row > 0]
        picks = row.nlargest(k)
        if len(picks):
            for s, _ in picks.items():
                W[t, cols.index(s)] = 1.0 / len(picks)
        rest = 1.0 - W[t].sum()
        if rest > 1e-9:
            W[t, cols.index(cash or safe)] = rest
    return W


def tgt_core_satellite(C, dates, core="SPY", sats=("GLD", "IEF", "TLT", "DBC"), core_w=0.6,
                       ma=200, vol_target=0.12, vol_win=20, band=0.10, cash="BIL", inv_vol=True):
    cols = list(C.columns)
    R = build(C)
    R_ = pd.DataFrame(R, index=dates, columns=cols)
    above = {s: (C[s] > C[s].rolling(ma).mean()) for s in [core, *sats] if s in C}
    vol = {s: R_[s].rolling(vol_win).std() for s in [core, *sats] if s in C}
    W = np.zeros_like(C.values)
    last_sc = 1.0
    for t in range(len(dates)):
        w = {}
        if above[core].iloc[t]:
            w[core] = core_w
        else:
            w[core] = 0.3 * core_w
        live = [s for s in sats if s in above and above[s].iloc[t] and np.isfinite(vol[s].iloc[t]) and vol[s].iloc[t] > 0]
        if live:
            if inv_vol:
                iv = {s: 1.0 / max(vol[s].iloc[t], 1e-6) for s in live}
                tot = sum(iv.values())
                for s in live:
                    w[s] = (1 - core_w) * iv[s] / tot
            else:
                for s in live:
                    w[s] = (1 - core_w) / len(live)
        # vol target on the book
        if vol_target and t > vol_win + 1:
            pw = np.array([w.get(c, 0.0) for c in cols])
            port = R[max(0, t - vol_win + 1):t + 1] @ pw
            v = port.std(ddof=1) * np.sqrt(TD)
            if np.isfinite(v) and v > 0:
                raw = min(1.0, vol_target / v)
                if abs(raw - last_sc) > band:
                    last_sc = raw
        for s, x in w.items():
            W[t, cols.index(s)] = x * last_sc
        W[t, cols.index(cash)] += 1.0 - W[t].sum()
    return W


if __name__ == "__main__":
    OFF = ["SPY", "QQQ", "IWM", "EFA", "EEM", "XLK", "XLV", "XLF", "XLE", "XLY", "XLP", "XLI", "XLU", "XLB"]
    DEF = ["IEF", "TLT", "SHY", "GLD"]
    syms = sorted(set(OFF + DEF + ["BIL", "DBC", "SPY"]))
    C, VIX = get_data(syms + ["^VIX"])
    dates = C.index

    spy = pd.DataFrame({"ret": C["SPY"].pct_change().fillna(0), "gross": 0, "turn": 0,
                        "inv": 1.0}, index=dates)
    report("SPY buy & hold", spy)

    for f in ("D", "W", "M"):
        report(f"SPY 200d MA timing ({f})", simulate(C, tgt_ma_timing(C), dates, f))

    for f in ("D", "W", "M"):
        report(f"sector top3 mom126 ({f})", simulate(C, tgt_topk_mom(C, dates, OFF, 3), dates, f))
    for f in ("W", "M"):
        report(f"sector top3 mom126 +200ma ({f})", simulate(C, tgt_topk_mom(C, dates, OFF, 3, ma=200), dates, f))
        report(f"sector top5 mom126 ({f})", simulate(C, tgt_topk_mom(C, dates, OFF, 5), dates, f))
        report(f"broad top2 mom252 ({f})", simulate(C, tgt_topk_mom(C, dates, ["SPY", "QQQ", "IWM", "EFA", "EEM"], 2, mom=252), dates, f))

    for f in ("W", "M"):
        report(f"core-satellite 60/40 vol12 ({f})", simulate(C, tgt_core_satellite(C, dates), dates, f))
        report(f"core-satellite 70% vol10 ({f})",
               simulate(C, tgt_core_satellite(C, dates, core_w=0.7, vol_target=0.10), dates, f))
        report(f"core-satellite 70% noVolT ({f})",
               simulate(C, tgt_core_satellite(C, dates, core_w=0.7, vol_target=None), dates, f))
        report(f"core-satellite 70% vol12 eqsat ({f})",
               simulate(C, tgt_core_satellite(C, dates, core_w=0.7, inv_vol=False), dates, f))


def tgt_tsmom(C, dates, universe, ma=200, top_n=None, vol_target=0.10, vol_win=20,
              band=0.10, cash="BIL", inv_vol=True, mom=126, skip=0, max_per=0.35):
    """Diversified time-series momentum: each asset held only in its own uptrend."""
    cols = list(C.columns)
    R = pd.DataFrame(build(C), index=dates, columns=cols)
    vol = {s: R[s].rolling(vol_win).std() for s in universe if s in C}
    above = {s: (C[s] > C[s].rolling(ma).mean()) for s in universe if s in C}
    momo = {s: (C[s].shift(skip) / C[s].shift(skip + mom) - 1) for s in universe if s in C}
    W = np.zeros_like(C.values)
    last_sc = 1.0
    ci = cols.index(cash)
    for t in range(len(dates)):
        live = [s for s in universe if s in above and above[s].iloc[t]]
        if top_n and len(live) > top_n:
            live = sorted(live, key=lambda s: -np.nan_to_num(momo[s].iloc[t], nan=-9))[:top_n]
        live = [s for s in live if np.isfinite(vol[s].iloc[t]) and vol[s].iloc[t] > 0]
        if live:
            if inv_vol:
                iv = {s: 1.0 / vol[s].iloc[t] for s in live}
                tot = sum(iv.values())
                w = {s: iv[s] / tot for s in live}
            else:
                w = {s: 1.0 / len(live) for s in live}
        else:
            w = {}
        if vol_target and t > vol_win + 1:
            pw = np.array([w.get(c, 0.0) for c in cols])
            port = build(C)[max(0, t - vol_win + 1):t + 1] @ pw
            v = port.std(ddof=1) * np.sqrt(TD)
            if np.isfinite(v) and v > 0:
                raw = min(1.0, vol_target / v)
                if abs(raw - last_sc) > band:
                    last_sc = raw
        for s, x in w.items():
            W[t, cols.index(s)] = min(x * last_sc, max_per)
        W[t, ci] += 1.0 - W[t].sum()
    return W


if __name__ == "__main__":
    C2, VIX2 = get_data(sorted(set(OFF + DEF + ["BIL", "DBC", "SPY"])) + ["^VIX"])
    dates2 = C2.index
    print("\n--- diversified time-series momentum ---")
    UNI = ["SPY", "QQQ", "IWM", "EFA", "EEM", "XLK", "XLV", "XLF", "XLE", "XLY", "XLP", "XLI", "XLU", "XLB",
           "GLD", "TLT", "IEF", "DBC"]
    for ma in (150, 200, 250):
        for vt in (None, 0.10, 0.12):
            report(f"TSMOM all uni ma{ma} vt{vt}", simulate(C2, tgt_tsmom(C2, dates2, UNI, ma=ma, vol_target=vt), dates2, "W"))
    for n in (5, 8):
        report(f"TSMOM top{n} ma200 vt10", simulate(C2, tgt_tsmom(C2, dates2, UNI, top_n=n, vol_target=0.10), dates2, "W"))
    report("TSMOM top6 eqweight ma200", simulate(C2, tgt_tsmom(C2, dates2, UNI, top_n=6, inv_vol=False, vol_target=0.10), dates2, "W"))
    report("TSMOM top6 ma200 vt10 (M)", simulate(C2, tgt_tsmom(C2, dates2, UNI, top_n=6, vol_target=0.10), dates2, "M"))
