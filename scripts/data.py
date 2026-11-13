"""Where the prices come from.
==============================

READ THIS FIRST IF YOU ARE NOT A CODER
--------------------------------------
The bot needs daily closing prices for a handful of tickers (SPY, QQQ, GLD and
so on). This file fetches them from Yahoo Finance — free, no account, no API key
— and keeps a copy on disk so that repeated runs are fast and offline-friendly.

Two modes:
    * normal  : use the saved copy in data/cache/ if it exists (fast, offline);
    * refresh : download fresh numbers from Yahoo first (what the live bot does,
                so it never decides on stale prices).

The saved copies are deliberately NOT uploaded to GitHub (see .gitignore): they
are large, they change daily, and they can be regenerated at any time. The live
workflow re-downloads them on every run.

A note on "adjusted" prices: Yahoo's adjusted close folds in dividends and stock
splits, so a long history is comparable over time. We use it everywhere, because
a strategy that ignores dividends systematically understates returns.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

log = logging.getLogger(__name__)

# Folder layout: <repo>/data/cache/SPY_daily.csv and so on.
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)


def _cache_path(symbol: str) -> Path:
    """The file on disk for one ticker. '^VIX' becomes 'VIX_daily.csv'.

    The caret is stripped because file names starting with it are awkward on
    some systems.
    """
    return CACHE / f"{symbol.replace('^', '').replace('/', '_')}_daily.csv"


def _download(symbol: str, start: str = "2004-06-01", tries: int = 3) -> Optional[pd.Series]:
    """Fetch one ticker from Yahoo Finance and save it; return daily closing prices.

    Retries a few times because Yahoo occasionally rate-limits, then saves a CSV
    with the columns date, open, high, low, close, volume. Returns None if all
    attempts fail — the caller then falls back to whatever is already saved.
    """
    try:
        import yfinance as yf
    except ImportError:  # pragma: no cover
        log.warning("yfinance not installed; using cache only")
        return None
    for attempt in range(1, tries + 1):
        try:
            df = yf.download(symbol, start=start, progress=False, auto_adjust=True, threads=False)
            if df is None or df.empty:
                raise RuntimeError("empty response")
            # yfinance returns two-level column headers; keep the useful level.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            df.columns = [str(c).lower() for c in df.columns]
            date_col = "date" if "date" in df.columns else df.columns[0]
            df = df.rename(columns={date_col: "date", "adj close": "close"})
            df["date"] = pd.to_datetime(df["date"])
            df = df[["date", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
            df = df.sort_values("date")
            df.to_csv(_cache_path(symbol), index=False)
            return df.set_index("date")["close"]
        except Exception as exc:  # network hiccup, rate limit, ...
            log.warning("download %s attempt %d/%d failed: %s", symbol, attempt, tries, exc)
            time.sleep(2 * attempt)
    return None


def load_prices(symbols: Iterable[str], start: str = "2007-06-01", end: Optional[str] = None,
                refresh: bool = False) -> pd.DataFrame:
    """Return one wide table of closing prices: rows = days, columns = tickers.

    Args:
        symbols: e.g. ["SPY", "QQQ", "GLD", ...]
        start / end: the date range to keep (YYYY-MM-DD).
        refresh: True → download fresh data first (used by the live bot).

    A ticker that cannot be loaded is skipped with a warning rather than
    crashing the run; the strategy already handles missing data by treating
    that ticker as "do not buy".
    """
    series = {}
    for sym in symbols:
        path = _cache_path(sym)
        s = None
        if refresh or not path.exists():
            s = _download(sym)
        if s is None and path.exists():
            # Fall back to the saved copy.
            df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
            s = pd.to_numeric(df["close"], errors="coerce").dropna()
        if s is None or s.empty:
            log.warning("no data for %s — it will be skipped", sym)
            continue
        series[sym] = s
    if not series:
        # Nothing on disk and nothing downloadable: almost always a missing
        # library or no internet. Say so plainly instead of raising a cryptic
        # "no price data available".
        hint = ""
        try:
            import yfinance  # noqa: F401
        except ImportError:
            hint = ("\n   The downloader library is missing. Run:\n"
                    "       pip install -r requirements-live.txt")
        raise RuntimeError(
            "no price data available."
            + hint
            + "\n   A fresh copy of this repo downloads prices on first use, which needs"
              " an internet connection.")
    panel = pd.DataFrame(series).sort_index()
    panel = panel.loc[start:end] if end else panel.loc[start:]
    return panel


def load_vix(start: str = "2007-06-01", end: Optional[str] = None,
             refresh: bool = False) -> Optional[pd.Series]:
    """The VIX "fear index", used only for logging/diagnostics, never for trades.

    Returning None instead of raising keeps the bot running if this optional
    extra is unavailable.
    """
    try:
        return load_prices(["^VIX"], start=start, end=end, refresh=refresh).iloc[:, 0]
    except Exception:
        return None


def universe(extra: Optional[List[str]] = None) -> List[str]:
    """The list of tickers the bot needs — taken from the strategy settings.

    Keeping it derived (not hard-coded) means changing the universe in
    ATAConfig automatically changes what gets downloaded, with no second place
    to remember.
    """
    from src.strategies.adaptive_trend import ATAConfig

    cfg = ATAConfig()
    syms = set(cfg.core_symbols()) | set(cfg.satellites) | {cfg.cash_symbol, "SPY", "QQQ", "IEF"}
    if extra:
        syms |= set(extra)
    return sorted(syms)
