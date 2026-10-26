"""Load MES price data: Yahoo Finance (1-minute bars, last ~29 days) or a CSV export."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

TZ = "America/Chicago"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=lambda c: str(c).strip().lower())
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(TZ)
    df = df[~df.index.duplicated()].sort_index()
    return df.dropna(subset=["open", "high", "low", "close"])


def download_yahoo(symbol: str = "MES=F", days: int = 29) -> pd.DataFrame:
    """Yahoo only serves 1-minute bars for the last ~30 days, in 7-day chunks."""
    import yfinance as yf

    days = min(days, 29)
    end = datetime.now(timezone.utc)
    cur = end - timedelta(days=days)
    parts = []
    while cur < end:
        nxt = min(cur + timedelta(days=7), end)
        d = yf.download(symbol, start=cur, end=nxt, interval="1m",
                        progress=False, auto_adjust=False, prepost=True)
        if not d.empty:
            parts.append(_clean(d))
        cur = nxt
    if not parts:
        raise RuntimeError(f"Yahoo returned no data for {symbol}. Check your internet "
                           "connection, or export a CSV from NinjaTrader and use --csv.")
    df = pd.concat(parts)
    return df[~df.index.duplicated()].sort_index()


def load_csv(path: str, csv_tz: str = TZ) -> pd.DataFrame:
    """Reads a NinjaTrader export (yyyyMMdd HHmmss;O;H;L;C;V) or a TradingView-style CSV."""
    first = Path(path).read_text().splitlines()[0]
    if ";" in first:
        df = pd.read_csv(path, sep=";", header=None,
                         names=["time", "open", "high", "low", "close", "volume"])
        idx = pd.DatetimeIndex(pd.to_datetime(df.pop("time"), format="%Y%m%d %H%M%S"))
        # NinjaTrader stamps a bar at its CLOSE; shift to bar-open time like Yahoo/TradingView.
        step = pd.Series(idx).diff().median()
        idx = idx - step
    else:
        df = pd.read_csv(path)
        names = {c.strip().lower(): c for c in df.columns}
        tcol = next((names[k] for k in ("time", "datetime", "date", "timestamp", "date/time")
                     if k in names), None)
        if tcol is None:
            raise ValueError("CSV needs a time/date column")
        t = df.pop(tcol)
        if pd.api.types.is_numeric_dtype(t):
            idx = pd.DatetimeIndex(pd.to_datetime(t, unit="s", utc=True))
        else:
            parsed = pd.to_datetime(t, errors="coerce")
            if not isinstance(parsed.dtype, pd.DatetimeTZDtype) and parsed.dtype == object:
                parsed = pd.to_datetime(t, utc=True)
            idx = pd.DatetimeIndex(parsed)
    if idx.tz is None:
        idx = idx.tz_localize(csv_tz, ambiguous="NaT", nonexistent="NaT")
    df.index = idx
    df = df[df.index.notna()]
    return _clean(df)


def load_data(csv: str | None = None, days: int = 29, refresh: bool = False,
              csv_tz: str = TZ, symbol: str = "MES=F") -> pd.DataFrame:
    if csv:
        return load_csv(csv, csv_tz)
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{symbol.replace('=', '_')}_1m.csv"
    fresh = cache.exists() and date.fromtimestamp(cache.stat().st_mtime) == date.today()
    if fresh and not refresh:
        df = pd.read_csv(cache, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        return _clean(df)
    print(f"Downloading {symbol} 1-minute bars for the last {min(days, 29)} days...")
    df = download_yahoo(symbol, days)
    df.to_csv(cache)
    return df
