"""Research harness v4 — production candidate: Adaptive Trend Allocation (ATA).

Sleeves
  core      : broad equity beta (SPY/QQQ), each gated by its own 200d trend
  satellite : diversifiers (GLD, IEF, TLT, DBC), trend-gated, inverse-vol weights
  ballast   : SHY/BIL when nothing else qualifies
Overlays
  * volatility targeting with hysteresis band and a floor
  * optional portfolio drawdown throttle (cumulative-throttle)
  * optional fast risk-off (VIX spike / 5-day crash) with cooldown
"""
import itertools
import numpy as np
import pandas as pd

from scripts.backtest import metrics
from scripts.research_sector_momentum import get_data, build, simulate

TD = 252
SYMS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF", "SHY", "BIL", "DBC", "XLK", "XLV",
        "XLF", "XLE", "XLY", "XLP", "XLI", "XLU", "XLB", "TIP", "LQD", "HYG", "VNQ", "SMH"]


def ata_targets(C, VIX, dates, core=(("SPY", 0.45), ("QQQ", 0.25)),
                sats=("GLD", "IEF", "TLT", "DBC"), sat_total=0.30, ma=200, mom=126, skip=0,
                vol_target=0.12, vol_win=20, band=0.10, floor=0.30,
                dd_throttle=0.0, dn=0.15, cooldown_days=0,
                fast_vix=None, fast_dd=None, fast_cut=0.5, fast_cool=5,
                cash="BIL", max_per=0.45, inv_vol=True):
    cols = list(C.columns)
    ix = {c: i for i, c in enumerate(cols)}
    R = build(C)
    Rf = pd.DataFrame(R, index=dates, columns=cols)
    vol = {c: Rf[c].rolling(vol_win).std() for c in cols}
    trend = {c: C[c] > C[c].rolling(ma).mean() for c in cols}
    momo = {c: (C[c].shift(skip) / C[c].shift(skip + mom) - 1) for c in cols}
    vix = VIX.reindex(dates).ffill().values if VIX is not None else None
    spy = C["SPY"].values

    W = np.zeros((len(dates), len(cols)))
    last_sc, eq, peak = 1.0, 1.0, 1.0
    fast_until = -1

    for t in range(len(dates)):
        if t < max(ma, mom + skip):
            W[t, ix[cash]] = 1.0
            continue
        w = {}
        # ---- core sleeve (each leg independently trend-gated) -------------
        for s, wt in core:
            if trend[s].iloc[t] and (momo[s].iloc[t] > 0 if not np.isnan(momo[s].iloc[t]) else False):
                w[s] = w.get(s, 0.0) + wt
        # ---- satellite sleeve (trend-gated, inverse vol) ------------------
        live = [s for s in sats if trend[s].iloc[t] and np.isfinite(vol[s].iloc[t]) and vol[s].iloc[t] > 0]
        if live:
            if inv_vol:
                iv = {s: 1.0 / vol[s].iloc[t] for s in live}
                tot = sum(iv.values())
                for s in live:
                    w[s] = w.get(s, 0.0) + sat_total * iv[s] / tot
            else:
                for s in live:
                    w[s] = w.get(s, 0.0) + sat_total / len(live)
        # ---- fill the rest with cash-equivalent ---------------------------
        # (de-risked core weight becomes cash, not leverage)
        # ---- fast risk-off ------------------------------------------------
        trig = False
        if fast_vix and vix is not None and vix[t] > fast_vix:
            trig = True
        if fast_dd and t > 5 and spy[t] / spy[t - 5] - 1 < fast_dd:
            trig = True
        if trig:
            fast_until = t + fast_cool
        if t <= fast_until and fast_cut < 1.0:
            w = {k: v * fast_cut for k, v in w.items()}
        # ---- volatility target -------------------------------------------
        if vol_target and t > vol_win + 1:
            pw = np.array([w.get(c, 0.0) for c in cols])
            port = R[t - vol_win + 1:t + 1] @ pw
            v = port.std(ddof=1) * np.sqrt(TD)
            if np.isfinite(v) and v > 1e-9:
                raw = min(1.0, vol_target / v)
                if abs(raw - last_sc) > band:
                    last_sc = raw
        # ---- drawdown throttle -------------------------------------------
        throttle = 1.0
        if dd_throttle:
            dd = eq / peak - 1.0
            if dd < -dd_throttle:
                throttle = 0.5
            if dd > -dd_throttle / 2:      # recovery resets the throttle
                throttle = 1.0
        scale = last_sc * throttle
        for s, x in w.items():
            W[t, ix[s]] = min(x * scale, max_per)
        W[t, ix[cash]] += 1.0 - W[t].sum()
        # ---- update the equity path used by the throttle ------------------
        if t + 1 < len(dates):
            r_next = float(np.dot(W[t], R[t + 1]))
            eq *= 1.0 + r_next
            peak = max(peak, eq)
    return W


def run(name, C, VIX, dates, freq="W", cost_bps=10.0, **kw):
    W = ata_targets(C, VIX, dates, **kw)
    sim = simulate(C, W, dates, freq, cost_bps=cost_bps)
    m, g = metrics(sim["ret"]), metrics(sim["gross"])
    print(f"{name:<46} sh {m['sharpe']:5.2f} sot {m['sortino']:5.2f} cagr {m['cagr']*100:5.1f}% "
          f"dd {m['max_dd']*100:6.1f}% calm {m['calmar']:4.2f} turn {sim['turn'].mean()*100:4.1f}%/d "
          f"| Gsh {g['sharpe']:4.2f}", flush=True)
    return sim, m


if __name__ == "__main__":
    C, VIX = get_data(SYMS + ["^VIX"])
    dates = C.index
    print("sample", dates[0].date(), dates[-1].date())

    base = run("BASE core45/25 sat30 vt12", C, VIX, dates)
    run("no vol target", C, VIX, dates, vol_target=None)
    run("vt15", C, VIX, dates, vol_target=0.15)
    run("vt10", C, VIX, dates, vol_target=0.10)
    run("sat20", C, VIX, dates, sat_total=0.20)
    run("sat40", C, VIX, dates, sat_total=0.40)
    run("core SPY100%", C, VIX, dates, core=(("SPY", 1.0),), sat_total=0.0)
    run("core SPY70 QQQ30", C, VIX, dates, core=(("SPY", 0.7), ("QQQ", 0.3)))
    run("sats +EFA +EEM", C, VIX, dates, sats=("GLD", "IEF", "TLT", "DBC", "EFA"))
    run("sats GLD/IEF only", C, VIX, dates, sats=("GLD", "IEF"))
    run("+ fast riskoff vix35", C, VIX, dates, fast_vix=35, fast_cut=0.5, fast_cool=5)
    run("+ fast riskoff vix30", C, VIX, dates, fast_vix=30, fast_cut=0.5, fast_cool=10)
    run("+ dd throttle 10%", C, VIX, dates, dd_throttle=0.10)
    run("+ dd throttle 15%", C, VIX, dates, dd_throttle=0.15)
    run("eq-weight sats", C, VIX, dates, inv_vol=False)
    run("monthly", C, VIX, dates, freq="M")
    run("ma250", C, VIX, dates, ma=250)
    run("ma150", C, VIX, dates, ma=150)
    run("2x costs", C, VIX, dates, cost_bps=20)

    print("\n--- parameter plateau: Sharpe ---")
    rows = []
    for cw, st, vt in itertools.product([(("SPY", 0.5), ("QQQ", 0.2)), (("SPY", 0.45), ("QQQ", 0.25)),
                                         (("SPY", 0.6), ("QQQ", 0.15)), (("SPY", 0.4), ("QQQ", 0.2))],
                                        [0.2, 0.3, 0.4], [0.08, 0.10, 0.12, 0.15]):
        W = ata_targets(C, VIX, dates, core=cw, sat_total=st, vol_target=vt)
        sim = simulate(C, W, dates, "W")
        m = metrics(sim["ret"])
        rows.append({"core": cw[0][1] + cw[1][1], "sat": st, "vt": vt,
                     "sharpe": m["sharpe"], "cagr": m["cagr"], "dd": m["max_dd"]})
    df = pd.DataFrame(rows)
    print(df.pivot_table(index=["core", "sat"], columns="vt", values="sharpe").round(2).to_string())
    print("\nDD distribution across grid:", df["dd"].describe()[["min", "50%", "max"]].round(3).to_dict())


def extra():
    C, VIX = get_data(SYMS + ["^VIX"])
    dates = C.index
    print("\n--- satellite / core variants ---")
    run("sats GLD IEF TLT (no DBC)", C, VIX, dates, sats=("GLD", "IEF", "TLT"))
    run("sats GLD IEF TLT DBC TIP", C, VIX, dates, sats=("GLD", "IEF", "TLT", "DBC", "TIP"))
    run("sats GLD IEF TLT LQD HYG", C, VIX, dates, sats=("GLD", "IEF", "TLT", "LQD", "HYG"))
    run("sats SHY IEF GLD", C, VIX, dates, sats=("SHY", "IEF", "GLD"))
    run("core SPY40 QQQ30 IWM10", C, VIX, dates, core=(("SPY", 0.40), ("QQQ", 0.30), ("IWM", 0.10)))
    run("core SPY50 QQQ20 IWM10", C, VIX, dates, core=(("SPY", 0.50), ("QQQ", 0.20), ("IWM", 0.10)))
    run("core SPY50 QQQ30 IWM20", C, VIX, dates, core=(("SPY", 0.5), ("QQQ", 0.3), ("IWM", 0.2)), sat_total=0.2)
    run("mom filter off (MA only)", C, VIX, dates, mom=5)
    run("skip=5 momentum", C, VIX, dates, skip=5)
    run("cost 5bp", C, VIX, dates, cost_bps=5)
    print("\n--- sub-periods for BASE ---")
    W = ata_targets(C, VIX, dates)
    sim = simulate(C, W, dates, "W")
    for a, b in [("2007", "2011"), ("2012", "2016"), ("2017", "2020"), ("2021", "2026")]:
        m = metrics(sim["ret"].loc[a:b])
        s = metrics(C["SPY"].pct_change().fillna(0).loc[a:b])
        print(f"{a}-{b}: ATA sh {m['sharpe']:.2f} cagr {m['cagr']*100:.1f}% dd {m['max_dd']*100:.1f}%  |  "
              f"SPY sh {s['sharpe']:.2f} cagr {s['cagr']*100:.1f}% dd {s['max_dd']*100:.1f}%")
    yt = (1 + sim["ret"]).groupby(sim.index.year).prod() - 1
    ys = (1 + C["SPY"].pct_change().fillna(0)).groupby(dates.year).prod() - 1
    print("\nyear  ATA    SPY")
    for y in yt.index:
        print(f"{y}  {yt[y]*100:6.1f} {ys[y]*100:6.1f}")
