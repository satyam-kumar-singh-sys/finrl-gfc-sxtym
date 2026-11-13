"""Research harness v3 — trend-gated core with defensive ballast + vol targeting."""
import numpy as np
import pandas as pd

from scripts.backtest import metrics
from scripts.research_sector_momentum import get_data, build, simulate

TD = 252
OFF = ["SPY", "QQQ", "IWM", "EFA", "EEM", "XLK", "XLV", "XLF", "XLE", "XLY", "XLP", "XLI", "XLU", "XLB"]
DEF = ["IEF", "TLT", "SHY", "GLD"]
UNI = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF", "SHY", "BIL", "DBC"]


def tgt_gated_core(C, dates, risk=("SPY",), risk_w=(1.0,), ma=200, mom=126, skip=0,
                   ballast=("IEF",), ballast_w=0.0, vol_target=0.12, vol_win=20,
                   band=0.10, cash="BIL", max_w=0.5, dual=True):
    """Risk sleeve held only while each asset is in its own uptrend (MA + momentum)."""
    cols = list(C.columns)
    R = build(C)
    Rf = pd.DataFrame(R, index=dates, columns=cols)
    vol = {s: Rf[s].rolling(vol_win).std() for s in cols}
    ci = cols.index(cash)
    W = np.zeros_like(C.values)
    last = 1.0
    for t in range(len(dates)):
        w = {}
        for s, wt in zip(risk, risk_w):
            ok = C[s].iloc[t] > C[s].iloc[:t + 1].rolling(ma).mean().iloc[t]
            if dual and t > mom + skip:
                ok = ok and (C[s].iloc[t - skip] / C[s].iloc[t - skip - mom] - 1) > 0
            if ok:
                w[s] = w.get(s, 0.0) + wt
        # ballast: only if in its own uptrend, else fall through to cash
        if sum(w.values()) < 1.0 - 1e-9:
            free = 1.0 - sum(w.values())
            b = {}
            for s in ballast:
                ok = C[s].iloc[t] > C[s].iloc[:t + 1].rolling(ma).mean().iloc[t]
                if ok:
                    b[s] = 0.0
            if b:
                if ballast_w:
                    share = min(free, ballast_w)
                    for s in b:
                        b[s] = share / len(b)
                else:
                    for s in b:
                        b[s] = free / len(b)
                w.update(b)
        # vol targeting on the intended book
        if vol_target and t > vol_win + 1:
            pw = np.array([w.get(c, 0.0) for c in cols])
            port = R[max(0, t - vol_win + 1):t + 1] @ pw
            v = port.std(ddof=1) * np.sqrt(TD)
            if np.isfinite(v) and v > 1e-9:
                raw = min(1.0, vol_target / v)
                if abs(raw - last) > band:
                    last = raw
        for s, x in w.items():
            W[t, cols.index(s)] = x * last
        W[t, ci] += 1.0 - W[t].sum()
    return W


def full(name, C, dates, W, freq="W"):
    sim = simulate(C, W, dates, freq)
    m, g = metrics(sim["ret"]), metrics(sim["gross"])
    print(f"{name:<44} sh {m['sharpe']:5.2f} sot {m['sortino']:5.2f} cagr {m['cagr']*100:5.1f}% "
          f"dd {m['max_dd']*100:6.1f}% calmar {m['calmar']:4.2f} turn {sim['turn'].mean()*100:4.1f}%/d "
          f"inv {sim['inv'].mean():4.2f}", flush=True)
    return sim, m


if __name__ == "__main__":
    C, VIX = get_data(sorted(set(UNI + UNI)) + ["^VIX"])
    dates = C.index
    print("sample", dates[0].date(), dates[-1].date())
    full("SPY b&h", C, dates, np.tile(np.array([[1.0 if c == "SPY" else 0.0 for c in C.columns]]), (len(dates), 1)), "W")

    configs = {
        "A SPY gated -> BIL, vt12": dict(risk=("SPY",), ballast=(), vol_target=0.12),
        "A2 SPY gated -> BIL, no vt": dict(risk=("SPY",), ballast=(), vol_target=None),
        "B SPY gated -> IEF, vt12": dict(risk=("SPY",), ballast=("IEF",), vol_target=0.12),
        "C SPY+QQQ gated, vt12": dict(risk=("SPY", "QQQ"), risk_w=(0.7, 0.3), ballast=(), vol_target=0.12),
        "D SPY gated + 20% GLD, vt12": dict(risk=("SPY",), ballast=("GLD",), ballast_w=0.2, vol_target=0.12),
        "E SPY gated, vt10": dict(risk=("SPY",), ballast=(), vol_target=0.10),
        "F SPY gated, vt15": dict(risk=("SPY",), ballast=(), vol_target=0.15),
        "G SPY gated -> IEF, no vt": dict(risk=("SPY",), ballast=("IEF",), vol_target=None),
        "H no dual (MA only), vt12": dict(risk=("SPY",), ballast=(), vol_target=0.12, dual=False),
        "I SPY+GLD+IEF gated, vt12": dict(risk=("SPY", "GLD", "IEF"), risk_w=(0.6, 0.2, 0.2), ballast=(), vol_target=0.12),
    }
    store = {}
    for k, kw in configs.items():
        W = tgt_gated_core(C, dates, **kw)
        store[k] = full(k, C, dates, W)

    print("\n--- stability check (sub-period Sharpe) ---")
    for k, (sim, _) in store.items():
        r = sim["ret"]
        parts = []
        for a, b in [("2007", "2011"), ("2012", "2016"), ("2017", "2020"), ("2021", "2026")]:
            m = metrics(r.loc[a:b])
            parts.append(f"{m.get('sharpe', float('nan')):5.2f}")
        print(f"{k:<44} " + " ".join(parts))
