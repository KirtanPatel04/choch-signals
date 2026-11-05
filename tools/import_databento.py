"""Turn a Databento CME OHLCV-1m CSV download into front-month 1-minute bars the backtester reads.

The download has every contract month and calendar spread of each product (MES, MNQ, MYM, MGC, SIL).
Spreads are dropped; each CME session day (5 PM - 5 PM CT) uses the outright contract that traded the most
on the PREVIOUS session (no peeking at the day's own volume). Bars are written to data_long/<SYM>_F_1m.csv,
with the Yahoo cache in data/ appended after the Databento range, so the newest days match the daily pages.
data/ itself is never changed.

    .venv/bin/python tools/import_databento.py ~/Downloads/GLBX-20261103-XXXX      # folder or .csv file(s)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import signals.engine as E                                           # noqa: E402

ROOTS = ("MES", "MNQ", "MYM", "MGC", "SIL")
OUT = ROOT / "data_long"


def read(paths: list[Path]) -> pd.DataFrame:
    files = [f for p in paths for f in (sorted(p.rglob("*.csv")) if p.is_dir() else [p])]
    df = pd.concat([pd.read_csv(f, usecols=["ts_event", "open", "high", "low", "close", "volume", "symbol"]) for f in files])
    df["ts"] = pd.to_datetime(df.ts_event, utc=True).dt.tz_convert(E.TZ)
    return df.drop(columns="ts_event")


def front(df: pd.DataFrame, root: str) -> pd.DataFrame:
    d = df[df.symbol.str.fullmatch(rf"{root}[FGHJKMNQUVXZ]\d{{1,2}}")].copy()
    d["day"] = E.session_date(pd.DatetimeIndex(d.ts))
    vol = d.groupby(["day", "symbol"]).volume.sum().unstack(fill_value=0)
    pick = vol.idxmax(axis=1).shift(1).fillna(vol.idxmax(axis=1))        # yesterday's most traded contract
    d = d[d.symbol.to_numpy() == pick.reindex(d.day).to_numpy()]
    out = d.set_index("ts")[["open", "high", "low", "close", "volume"]].sort_index()
    out.index.name = "Datetime"
    return out[~out.index.duplicated()]


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    df = read([Path(p).expanduser() for p in sys.argv[1:]])
    OUT.mkdir(exist_ok=True)
    for r in ROOTS:
        bars = front(df, r)
        if bars.empty:
            print(f"{r}: no bars in the download")
            continue
        y = ROOT / "data" / f"{r}_F_1m.csv"
        if y.exists():
            yb = pd.read_csv(y, index_col=0)
            yb.index = pd.to_datetime(yb.index, utc=True).tz_convert(E.TZ)
            bars = pd.concat([bars, yb[yb.index > bars.index[-1]]])
        bars.to_csv(OUT / f"{r}_F_1m.csv")
        print(f"{r}: {len(bars):,} bars {bars.index[0]:%Y-%m-%d} -> {bars.index[-1]:%Y-%m-%d %H:%M} CT")


if __name__ == "__main__":
    main()
