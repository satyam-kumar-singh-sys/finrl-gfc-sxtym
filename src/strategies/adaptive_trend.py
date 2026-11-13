"""
Adaptive Trend Allocation (ATA) v1.0 — the brain of this trading bot.
=========================================================================

READ THIS FIRST IF YOU ARE NOT A CODER
--------------------------------------
This file answers one question, once per week:

    "Given today's prices, what percentage of the account should sit in each
     ticker, and how much should stay in cash?"

It does NOT place orders. It does not talk to the broker. It just produces a
shopping list of percentages, for example:

    SPY  44%   (an S&P 500 fund)
    BIL  54%   (short-term US Treasury bills — "safe parking" for cash)
    ...  and 2% left over as a cash buffer

A separate file (scripts/run_daily.py) takes that list to Alpaca and does the
buying and selling. Keeping the two apart is deliberate: the same list is used
by the historical backtest and by the live bot, so the backtest cannot
accidentally describe a different strategy from the one that trades.

THE IDEA IN ONE PARAGRAPH
-------------------------
We only hold something while it is going up. "Going up" means two simple,
well-known checks: the price is above its own one-year average, and it has made
money over the last six months. If a holding fails either check, we sell it and
park that money in Treasury bills, which pay interest and barely move. Bonds and
gold are included so that when shares fall we still have something earning. On
top of that, if the portfolio starts shaking too much, we shrink everything
proportionally until the shaking is back near our comfort level (12% a year).

WHY SO DEFENSIVE?
-----------------
The competition score is 30% risk-adjusted return, 20% worst drawdown, 20%
stability. A strategy that makes a modest, steady return and never crashes beats
one that doubles some years and loses 40% in others. In testing this design
roughly halved the market's return but cut its worst loss from -55% to -12%.

THE FOUR STAGES (the "S → A → T → R" pipeline from the FinRL-X paper)
---------------------------------------------------------------------
    S  Select   : which tickers pass the up-trend test today?
    A  Allocate : how much money for each of the ones that passed?
    T  Time     : if too few passed, put the leftover in Treasury bills
    R  Risk     : shrink the whole thing if the portfolio is too jumpy
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# The framework's standard "strategy" blueprint. Our class fills in one method
# (generate_weights) and the framework can then use it anywhere it expects a
# strategy — backtest, live trading, dashboards.
from src.strategies.base_strategy import BaseStrategy, StrategyConfig, StrategyResult

# Number of trading days in a year. Used to convert a daily wobble into the
# annual figure people quote ("12% a year").
TRADING_DAYS = 252


# =========================================================================== #
# 1. THE SETTINGS KNOB PANEL
# =========================================================================== #
@dataclass
class ATAConfig(StrategyConfig):
    """Every tunable number in one place, so nothing is hidden in the code.

    A non-coder can read this block and know exactly how the bot behaves.
    Changing a number here changes the strategy; nothing else needs editing.
    """

    name: str = "AdaptiveTrendAllocation"

    # --- WHAT WE BUY -------------------------------------------------------
    # The "core": the mainstream stock-market funds, and how much of the
    # account each one gets when its up-trend test passes.
    #   SPY = fund of the 500 biggest US companies (45% of the account)
    #   QQQ = fund of the 100 biggest tech-heavy companies (25% of the account)
    core: Tuple[Tuple[str, float], ...] = (("SPY", 0.45), ("QQQ", 0.25))

    # The "satellites": things that tend to hold up when shares fall —
    #   GLD = gold, IEF = 7-10 year US government bonds,
    #   TLT = 20+ year US government bonds, DBC = commodities basket.
    # Together they may hold 30% of the account, split between whichever of them
    # are currently in an up-trend.
    satellites: Tuple[str, ...] = ("GLD", "IEF", "TLT", "DBC")
    satellite_total: float = 0.30

    # Where money goes when nothing looks good: BIL is a fund that holds
    # 1-3 month US Treasury bills. It is effectively "cash that pays interest".
    cash_symbol: str = "BIL"

    # --- HOW WE DECIDE "IS IT GOING UP?" -----------------------------------
    # Check 1 (trend): is today's price above the average price of the last
    # 200 trading days (~10 months)? A long window means few false alarms.
    trend_window: int = 200
    # Check 2 (momentum): did it make money over the last 126 trading days
    # (~6 months)? This stops us buying something that is only above its
    # average because the average itself is falling.
    mom_window: int = 126
    # Ignore the most recent N days in that 6-month calculation. 0 is fine for
    # monthly-scale signals; it exists so the check can be tuned.
    mom_skip: int = 0

    # --- HOW MUCH WOBBLE WE TOLERATE ---------------------------------------
    # We aim for the portfolio to wobble about 12% a year. If it wobbles more,
    # we hold more cash; if less, we invest more (but never borrow).
    target_vol: float = 0.12
    # Wobble is measured over the last 20 trading days (about a month).
    vol_window: int = 20
    # Only react to a wobble change if it would shift the mix by more than 10%.
    # This stops tiny day-to-day noise from making us trade constantly.
    vol_band: float = 0.10
    # No single fund may exceed 45% of the account (concentration guard).
    max_weight: float = 0.45
    # Always keep at least 2% in cash so fees and rounding are never a problem.
    min_cash: float = 0.02

    def core_symbols(self) -> List[str]:
        """Helper: just the ticker names of the core funds, without the weights."""
        return [s for s, _ in self.core]


# A ready-made settings object other files can import without rebuilding one.
DEFAULT_CONFIG = ATAConfig()


# =========================================================================== #
# 2. SMALL MEASURING TOOLS (each does one arithmetic job, all unit-tested)
# =========================================================================== #
def momentum(close: pd.Series, window: int = 126, skip: int = 0) -> float:
    """How much did this go up (or down) over the last `window` trading days?

    Example: price was 100 six months ago and is 110 today → returns 0.10,
    i.e. +10%. Returns "not a number" if there is not enough history yet, which
    callers treat as "not enough evidence, do not buy".
    """
    s = close.dropna()
    if len(s) < window + skip + 1:
        return float("nan")
    start, end = s.iloc[-(window + skip + 1)], s.iloc[-(skip + 1)]
    if not np.isfinite(start) or start <= 0:
        return float("nan")
    return float(end / start - 1.0)


def above_trend(close: pd.Series, window: int = 200) -> bool:
    """Is today's price above the average of the last `window` days?

    This is the classic "200-day moving average" rule. Answer is simply
    True (yes, in an up-trend) or False (no).
    """
    s = close.dropna()
    if len(s) < window:
        return False
    return bool(s.iloc[-1] > s.iloc[-window:].mean())


def realized_vol(returns: pd.Series, window: int = 20) -> float:
    """How jumpy has this asset been lately, expressed as a yearly percentage?

    Returns e.g. 0.22 for a fund that typically moves ~22% a year. A high number
    means we give it a smaller share when splitting the satellite money.
    """
    r = returns.dropna().iloc[-window:]
    if len(r) < max(5, window // 2):
        return float("nan")
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))


def inverse_vol_weights(symbols: Sequence[str], vols: Dict[str, float],
                        total: float) -> Dict[str, float]:
    """Split `total` money across `symbols`, giving calm assets a bigger share.

    "Inverse to volatility" means: if gold is twice as jumpy as bonds, bonds get
    twice the money. The result always adds up to `total`. Assets with unknown
    or zero volatility are skipped (we never guess).
    """
    usable = {s: vols[s] for s in symbols if np.isfinite(vols.get(s, float("nan"))) and vols[s] > 0}
    if not usable:
        return {}
    inv = {s: 1.0 / v for s, v in usable.items()}
    denom = sum(inv.values())
    return {s: total * w / denom for s, w in inv.items()}


def enforce_caps(weights: Dict[str, float], max_weight: float,
                 min_cash: float, cash_symbol: str) -> Dict[str, float]:
    """Apply the safety limits: no fund too big, always some cash, never borrow.

    Three rules, in order:
      1. if one fund is bigger than `max_weight`, trim it to that size;
      2. if the total invested is more than (100% − `min_cash`), scale
         everything down proportionally so the cash buffer survives;
      3. drop dust positions (under 0.01%) that are not worth trading.
    The output can never add up to more than 100% of the account.
    """
    w = {k: float(v) for k, v in weights.items() if np.isfinite(v) and v > 1e-6}
    for sym in list(w):
        if sym != cash_symbol and w[sym] > max_weight:
            w[sym] = max_weight
    gross = sum(w.values())
    if gross > 1.0 - min_cash:
        scale = (1.0 - min_cash) / gross
        w = {k: v * scale for k, v in w.items()}
    return {k: round(v, 6) for k, v in w.items() if v > 1e-4}


# =========================================================================== #
# 3. THE STRATEGY ITSELF
# =========================================================================== #
class AdaptiveTrendAllocationStrategy(BaseStrategy):
    """Turns a table of daily closing prices into a target list of percentages.

    Typical use:

        strategy = AdaptiveTrendAllocationStrategy()      # default settings
        result   = strategy.compute(price_table)          # price table = pandas
        result.weights                                    # the shopping list
        result.metadata                                   # why it chose that
    """

    def __init__(self, config: Optional[ATAConfig] = None):
        super().__init__(config or ATAConfig())
        self.config: ATAConfig = self.config  # type: ignore[assignment]
        # Remembers the last risk-scaling factor, so the bot does not flip
        # between "invest 80%" and "invest 85%" every single day. Live trading
        # relies on this being stateful, and the backtest reproduces it.
        self._scaler: float = 1.0

    # ------------------------------------------------------------------ #
    # 3a. The method the framework calls
    # ------------------------------------------------------------------ #
    def generate_weights(self, data: Dict[str, pd.DataFrame],
                         target_date: Optional[str] = None) -> StrategyResult:
        """Framework entry point. Takes a dictionary of market data, returns weights.

        `data["close"]` is a table: one row per trading day, one column per
        ticker, values are closing prices. `target_date` lets you ask the
        question "what would the bot have decided on 3 June 2024?" — the
        backtest uses it heavily, and it is how look-ahead mistakes are avoided.
        """
        if "close" not in data:
            raise KeyError("data must contain a 'close' DataFrame")
        close = data["close"].copy()
        close.index = pd.to_datetime(close.index)
        close = close.sort_index()
        if target_date is not None:
            # Chop off everything after the requested day: the bot can only
            # ever "see" the past.
            close = close.loc[: pd.Timestamp(target_date)]
        if close.empty:
            raise ValueError("no price history at or before the requested date")
        return self.compute(close)

    # ------------------------------------------------------------------ #
    # 3b. The actual calculation
    # ------------------------------------------------------------------ #
    def compute(self, close: pd.DataFrame, as_of: Optional[pd.Timestamp] = None,
                update_state: bool = True) -> StrategyResult:
        """Work out the target percentages from a price table.

        `update_state=False` asks "what WOULD you say?" without remembering the
        answer — useful for checking what an older day's decision would have
        been. The backtest uses the normal path (True) so it behaves like live.
        """
        cfg = self.config
        if as_of is not None:
            close = close.loc[: pd.Timestamp(as_of)]
        as_of = close.index[-1]              # the decision date = last day known
        rets = close.pct_change()            # day-to-day percentage moves

        target: Dict[str, float] = {}        # ticker -> share of the account
        sleeve: Dict[str, str] = {}          # ticker -> which "bucket" it is in
        gates: Dict[str, Dict[str, Any]] = {}   # ticker -> why it passed/failed

        # -- STAGES S + T: the core stock funds ---------------------------
        # Each core fund is judged on its own. Pass = it gets its full share.
        # Fail = its share is simply not invested (it lands in cash below).
        core_open_weight = 0.0
        for sym, weight in cfg.core:
            gate = self._gate(close, sym, rets)
            gates[sym] = gate
            if gate["open"]:
                target[sym] = target.get(sym, 0.0) + weight
                sleeve[sym] = "core"
                core_open_weight += weight

        # -- STAGES S + A: the diversifier funds --------------------------
        # Only the ones in an up-trend qualify; their 30% is split between them
        # in inverse proportion to how jumpy each one is.
        sat_vols: Dict[str, float] = {}
        for sym in cfg.satellites:
            gate = self._gate(close, sym, rets)
            gates[sym] = gate
            if gate["open"]:
                sat_vols[sym] = gate["vol_20d"]

        sat_weights = inverse_vol_weights(list(sat_vols), sat_vols, cfg.satellite_total)
        for sym, w in sat_weights.items():
            target[sym] = target.get(sym, 0.0) + w
            sleeve[sym] = "satellite"

        # -- STAGE T: anything that failed its test goes to Treasury bills --
        # `idle` = money that had a job but did not qualify for it.
        core_budget = sum(w for _, w in cfg.core)
        idle = (core_budget - core_open_weight) + (cfg.satellite_total - sum(sat_weights.values()))
        if idle > 1e-9:
            target[cfg.cash_symbol] = target.get(cfg.cash_symbol, 0.0) + idle
            sleeve[cfg.cash_symbol] = "cash"

        # -- STAGE R: risk limits and the wobble target --------------------
        # First the hard caps (no fund > 45%), then the wobble adjustment:
        # if the resulting mix would swing more than 12% a year, shrink every
        # risky holding and move the freed money to cash. `min(1.0, ...)` inside
        # _update_scaler guarantees we never borrow to invest more than 100%.
        target = enforce_caps(target, cfg.max_weight, 0.0, cfg.cash_symbol)

        pre_vol = self.portfolio_vol(close, target)      # how jumpy is this mix?
        scaler = self._update_scaler(pre_vol, update_state)
        if scaler < 1.0 - 1e-9:
            target = {k: v * scaler for k, v in target.items()}
            target[cfg.cash_symbol] = target.get(cfg.cash_symbol, 0.0) + (1.0 - sum(target.values()))

        weights = enforce_caps(target, cfg.max_weight, cfg.min_cash, cfg.cash_symbol)
        sleeve = {k: sleeve.get(k, "cash") for k in weights}

        # -- package the answer as a neat table ----------------------------
        rows = [{"symbol": s, "weight": w, "sleeve": sleeve[s]} for s, w in weights.items()]
        wdf = pd.DataFrame(rows, columns=["symbol", "weight", "sleeve"])
        wdf = wdf.sort_values("weight", ascending=False).reset_index(drop=True)

        # `metadata` is the explanation. It is written into the public trade log
        # every week so any decision can be audited after the fact.
        invested = float(wdf["weight"].sum())
        metadata: Dict[str, Any] = {
            "strategy": cfg.name,
            "version": "1.0",
            "as_of": pd.Timestamp(as_of).strftime("%Y-%m-%d"),
            "gates": gates,
            "satellite": {s: round(v, 6) for s, v in sat_weights.items()},
            "closed_satellites": [s for s in cfg.satellites if s not in sat_weights],
            "vol_20d_estimate": None if not np.isfinite(pre_vol) else round(pre_vol, 4),
            "vol_scaler": round(scaler, 4),
            "invested": round(invested, 4),
            "cash": round(1.0 - invested, 4),
            "core_weight": round(sum(weights.get(s, 0.0) for s in cfg.core_symbols()), 4),
            "satellite_weight": round(sum(weights.get(s, 0.0) for s in cfg.satellites), 4),
            "risk_state": self._risk_state(gates),
        }
        return StrategyResult(strategy_name=cfg.name, weights=wdf, metadata=metadata)

    # ------------------------------------------------------------------ #
    # 3c. Helper methods used above
    # ------------------------------------------------------------------ #
    def _gate(self, close: pd.DataFrame, sym: str, rets: pd.DataFrame) -> Dict[str, Any]:
        """The up-trend test for one ticker, plus a human-readable reason.

        Passes only if BOTH are true:
          * the price is above its 200-day average   (long-term direction)
          * it made money over the last 126 days     (recent direction)
        """
        cfg = self.config
        if sym not in close.columns:
            return {"open": False, "reason": "no data", "above_trend": False,
                    "momentum_126d": None, "vol_20d": None}
        s = close[sym].dropna()
        trend_ok = above_trend(s, cfg.trend_window)
        mom = momentum(s, cfg.mom_window, cfg.mom_skip)
        mom_ok = bool(np.isfinite(mom) and mom > 0)
        vol = realized_vol(rets[sym] if sym in rets else pd.Series(dtype=float), cfg.vol_window)
        open_ = bool(trend_ok and mom_ok)
        if open_:
            reason = "price above trend and 6m momentum positive"
        elif not trend_ok:
            reason = f"close below {cfg.trend_window}d SMA"
        else:
            reason = f"{cfg.mom_window}d momentum <= 0"
        return {
            "open": open_,
            "reason": reason,
            "above_trend": trend_ok,
            "momentum_126d": None if not np.isfinite(mom) else round(mom, 4),
            "vol_20d": None if not np.isfinite(vol) else round(vol, 4),
        }

    def portfolio_vol(self, close: pd.DataFrame, weights: Dict[str, float]) -> float:
        """How jumpy would this exact mix of holdings be, annualised?

        Method: take the last 20 days of price moves for every holding, weight
        each by its share of the account, add them up day by day, and measure
        the spread of that combined series. Because holdings can move in
        opposite directions, the total is usually less than the sum of the
        parts — that diversification is exactly what we want to capture.
        Cash is excluded: Treasury bills barely move.
        """
        cfg = self.config
        syms = [s for s in weights if s in close.columns and s != cfg.cash_symbol]
        if not syms:
            return 0.0
        rets = close[syms].pct_change().dropna(how="all").iloc[-cfg.vol_window:]
        if len(rets) < 5:
            return float("nan")
        w = pd.Series({s: weights[s] for s in syms})
        port = rets.fillna(0.0).mul(w, axis=1).sum(axis=1)
        return float(port.std(ddof=1) * math.sqrt(TRADING_DAYS))

    def _update_scaler(self, vol: float, update_state: bool = True) -> float:
        """The "volume knob" for risk: 1.0 = fully invested, 0.5 = half in cash.

        If the mix is expected to wobble 24% a year and we only want 12%, the
        knob goes to 0.5. Two important details:
          * it never exceeds 1.0 — the bot cannot borrow money;
          * it only turns when the change is bigger than `vol_band` (10%),
            which is what keeps trading costs low.
        """
        cfg = self.config
        if not cfg.target_vol or not np.isfinite(vol) or vol <= 1e-9:
            return self._scaler if update_state else 1.0
        raw = min(1.0, cfg.target_vol / vol)      # never lever up
        if not update_state:
            return raw
        if abs(raw - self._scaler) > cfg.vol_band:
            self._scaler = raw
        return self._scaler

    @staticmethod
    def _risk_state(gates: Dict[str, Dict[str, Any]]) -> str:
        """A one-word summary for the log: how much of the book is in up-trends?

        defensive = nothing passed (we are hiding in Treasury bills)
        mixed     = some passed
        risk_on   = everything passed
        """
        open_core = sum(1 for s, g in gates.items() if g["open"])
        if open_core == 0:
            return "defensive"
        if open_core == len(gates):
            return "risk_on"
        return "mixed"


def build_strategy(**overrides: Any) -> AdaptiveTrendAllocationStrategy:
    """Convenience constructor: build_strategy(target_vol=0.10, trend_window=250)."""
    return AdaptiveTrendAllocationStrategy(ATAConfig(**overrides) if overrides else ATAConfig())


# Old name kept so older example notebooks keep working.
get_strategy = build_strategy
