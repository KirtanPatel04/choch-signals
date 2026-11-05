"""Well-known futures strategies vs our CHoCH plan on the Databento year (data_long/, Nov 3 2025 - Nov 2 2026).

Standard textbook settings, picked BEFORE looking at results (no tuning), so the year is a fair test:
  ORB 15 / ORB 30   Opening range breakout: high/low of the first 15 (30) minutes after 8:30 CT. First 1-minute
                    close outside it -> enter at the next open, stop at the other side, target 2R, else out 2:59 PM.
  ORB 30 + trend    Same, only in the direction of yesterday's close vs its 20-day average.
  Gap fade          8:30 open gapped >= 0.15% from yesterday's 3:00 PM close -> fade it at the open, target
                    yesterday's close, stop the same distance the other way, else out 2:59 PM.
  Late momentum     Gao-Han-Li-Zhou "intraday momentum": the move from yesterday's close to 9:00 sets the side
                    for the last 30 minutes (2:30 - 2:59 PM). No stop.
  Overnight drift   Indices only: buy the 3:00 PM close, sell the next 8:30 open (the "overnight" effect). Long only.
  Turtle 20/10      Daily bars: buy a 20-day high / sell a 20-day low, stop 2 ATR, exit on a 10-day low/high.
Every trade is 1 contract. Costs: $1.24 commission + 1 tick slippage on every market-order entry, stop and time
exit (limit targets fill at the price). Our plan is shown the same way (1 contract, plain 2R, so no half).

    .venv/bin/python tools/famous_strategies.py        # writes reports/famous_strategies.html and opens it
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import account_study as A                                            # noqa: E402
import plan_search as P                                              # noqa: E402
import signals.engine as E                                           # noqa: E402

A.DATA = ROOT / "data_long"
MKTS = ("MES", "MNQ", "MYM", "MGC", "SIL")
OPEN, CLOSE = 8 * 60 + 30, 15 * 60


def days(d: pd.DataFrame) -> dict:
    """Regular-hours bars (8:30 - 2:59 PM CT bar starts) per date, weekdays from Nov 3 2025."""
    m = d.index.hour * 60 + d.index.minute
    r = d[(m >= OPEN) & (m < CLOSE) & (d.index.weekday < 5)]
    r = r[(r.index >= P.FIRST) & (r.index < P.LAST)]
    return {k: g for k, g in r.groupby(r.index.date) if len(g) > 120}          # skip half days


def walk(g: pd.DataFrame, i: int, side: int, entry: float, stop: float | None, target: float | None, tick: float):
    """From bar i on: stop first if a bar hits both, then target, else out at the last bar's close. Returns (exit, kind)."""
    h, l = g.high.to_numpy(), g.low.to_numpy()
    for j in range(i, len(g)):
        if stop is not None and ((l[j] <= stop) if side == 1 else (h[j] >= stop)):
            o = g.open.iat[j]
            px = o if (j > i and ((o < stop) if side == 1 else (o > stop))) else stop
            return px - side * tick, "stop", g.index[j]
        if target is not None and ((h[j] >= target) if side == 1 else (l[j] <= target)):
            return target, "target", g.index[j]
    return g.close.iat[-1] - side * tick, "time", g.index[-1]


def trade(m, t, side, entry, exit_, risk=None, kind=""):
    tick, pv, comm = A.MK[m][0], A.MK[m][1], A.MK[m][2]
    usd = (exit_ - entry) * side * pv - comm
    return dict(market=m, time=t, side=side, usd=usd, r=(usd / (risk * pv)) if risk else None, kind=kind)


def orb(m, D, mins, trend=None):
    tick, out = A.MK[m][0], []
    for day, g in D.items():
        rng = g.iloc[:mins]
        hi, lo = rng.high.max(), rng.low.min()
        rest = g.iloc[mins:]
        c = rest.close.to_numpy()
        k = next((j for j in range(len(rest) - 1) if c[j] > hi or c[j] < lo), None)
        if k is None:
            continue
        side = 1 if c[k] > hi else -1
        if trend is not None and trend.get(day, 0) != side:
            continue
        entry = rest.open.iat[k + 1] + side * tick
        stop = lo if side == 1 else hi
        risk = (entry - stop) * side
        if risk <= 0:
            continue
        px, kind, _ = walk(rest, k + 1, side, entry, stop, entry + side * 2 * risk, tick)
        out.append(trade(m, rest.index[k + 1], side, entry, px, risk, kind))
    return out


def gap_fade(m, D, prev_close):
    tick, out = A.MK[m][0], []
    for day, g in D.items():
        pc = prev_close.get(day)
        if pc is None:
            continue
        o = g.open.iat[0]
        gap = (o - pc) / pc
        if abs(gap) < 0.0015:
            continue
        side = -1 if gap > 0 else 1
        entry = o + side * tick
        risk = abs(o - pc)
        px, kind, _ = walk(g, 0, side, entry, entry - side * risk, pc, tick)
        out.append(trade(m, g.index[0], side, entry, px, risk, kind))
    return out


def late_momentum(m, D, prev_close):
    tick, out = A.MK[m][0], []
    for day, g in D.items():
        pc = prev_close.get(day)
        t9 = g[g.index.hour * 60 + g.index.minute < 9 * 60]
        late = g[g.index.hour * 60 + g.index.minute >= 14 * 60 + 30]
        if pc is None or t9.empty or late.empty:
            continue
        side = 1 if t9.close.iat[-1] > pc else -1
        entry = late.open.iat[0] + side * tick
        out.append(trade(m, late.index[0], side, entry, late.close.iat[-1] - side * tick, None, "time"))
    return out


def overnight(m, D):
    tick, out, keys = A.MK[m][0], [], sorted(D)
    for a, b in zip(keys, keys[1:]):
        entry = D[a].close.iat[-1] + tick
        out.append(trade(m, D[a].index[-1], 1, entry, D[b].open.iat[0] - tick, None, "time"))
    return out


def turtle(m, d):
    """Daily bars per CME session (5 PM - 4 PM CT)."""
    tick, out = A.MK[m][0], []
    sd = E.session_date(d.index)
    b = d.groupby(sd).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    b.index = pd.to_datetime(b.index)
    b = b[b.index.weekday < 5]
    tr = pd.concat([b.high - b.low, (b.high - b.close.shift()).abs(), (b.low - b.close.shift()).abs()], axis=1).max(axis=1)
    atr, hi20, lo20 = tr.rolling(20).mean().shift(), b.high.rolling(20).max().shift(), b.low.rolling(20).min().shift()
    hi10, lo10 = b.high.rolling(10).max().shift(), b.low.rolling(10).min().shift()
    pos = None
    for t, x in b.iterrows():
        if np.isnan(atr[t]):
            continue
        if pos:
            side, entry, stop, t0 = pos
            ex = lo10[t] if side == 1 else hi10[t]
            lvl = max(stop, ex) if side == 1 else min(stop, ex)
            if (x.low <= lvl) if side == 1 else (x.high >= lvl):
                px = (min(x.open, lvl) if side == 1 else max(x.open, lvl)) - side * tick
                out.append(trade(m, t0, side, entry, px, 2 * atr[t0], "exit"))
                pos = None
            continue
        if t < P.FIRST.tz_localize(None) or t >= P.LAST.tz_localize(None):
            continue
        for side, lvl in ((1, hi20[t]), (-1, lo20[t])):
            if (x.high > lvl) if side == 1 else (x.low < lvl):
                entry = (max(x.open, lvl) if side == 1 else min(x.open, lvl)) + side * tick
                pos = (side, entry, entry - side * 2 * atr[t], t)
                break
    if pos:
        side, entry, stop, t0 = pos
        out.append(trade(m, t0, side, entry, b.close.iat[-1] - side * tick, 2 * atr[t0], "open"))
    return out


def our_plan(Dfull):
    """Our live plan, 1 contract a trade, plain 2R (1 contract can't be split), after commission."""
    taken = P.plan(sorted(P.signals(Dfull, 2.0), key=lambda s: s["time"]), P.GROUPS["indices 1 slot + MGC + SIL (ours)"],
                   ("A+", "A", "B"), 11, 510, 840, 1)
    out = []
    for s in taken:
        risk_usd = A.MK[s["market"]][2] / s["cost"]                            # cost = comm / risk$, so risk$ = comm / cost
        out.append(dict(market=s["market"], time=s["time"], side=s["side"], usd=s["r"] * risk_usd - A.MK[s["market"]][2],
                        r=s["r"] - s["cost"], kind=""))
    return out


UNIT: dict = {}                    # market -> median regular-hours day range in $ for 1 contract


def summarize(tr: list[dict]) -> dict:
    if not tr:
        return dict(n=0)
    df = pd.DataFrame(tr)
    df["u"] = df.usd / df.market.map(UNIT)                               # every market counts the same
    df["time"] = pd.to_datetime(df.time.astype(str).str[:19])
    df = df.sort_values("time")
    sec = df.time >= P.SPLIT.tz_localize(None)
    mon = df.groupby(df.time.dt.strftime("%Y-%m")).usd.sum()
    eq = df.usd.cumsum()
    return dict(n=len(df), win=float((df.usd > 0).mean()), usd=float(df.usd.sum()), h1=float(df.usd[~sec].sum()),
                h2=float(df.usd[sec].sum()), pos=int((mon > 0).sum()), months=len(mon), oct=float(mon.get("2026-10", 0)),
                dd=float((eq - eq.cummax()).min()), R=float(df.r.sum()) if df.r.notna().all() else None,
                monthly={k: round(float(v), 2) for k, v in mon.items()},
                curve=[[t.strftime("%Y-%m-%d"), round(float(v), 2)] for t, v in zip(df.time, eq)],
                by_mkt={m: round(float(v), 2) for m, v in df.groupby("market").usd.sum().items()},
                u1=float(df.u[~sec].sum()), u2=float(df.u[sec].sum()), t=float(df.u.mean() / df.u.std() * np.sqrt(len(df))),
                mkts_pos=int((df.groupby("market").u.sum() > 0).sum()), mkts=int(df.market.nunique()))


def main() -> None:
    Dfull = {m: A.load(m, False) for m in MKTS}
    S = {}
    for m in MKTS:
        D = days(Dfull[m])
        UNIT[m] = float(np.median([g.high.max() - g.low.min() for g in D.values()])) * A.MK[m][1]
        closes = pd.Series({k: g.close.iat[-1] for k, g in D.items()})
        prev_close = closes.shift().dropna().to_dict()
        ma = closes.rolling(20).mean()
        trend = np.sign(closes - ma).shift().dropna().to_dict()
        S.setdefault("ORB 15 min", []).extend(orb(m, D, 15))
        S.setdefault("ORB 30 min", []).extend(orb(m, D, 30))
        S.setdefault("ORB 30 min + 20-day trend", []).extend(orb(m, D, 30, trend))
        S.setdefault("Gap fade", []).extend(gap_fade(m, D, prev_close))
        S.setdefault("Late-day momentum", []).extend(late_momentum(m, D, prev_close))
        if m in ("MES", "MNQ", "MYM"):
            S.setdefault("Overnight drift (indices, long)", []).extend(overnight(m, D))
        S.setdefault("Turtle 20/10 breakout", []).extend(turtle(m, Dfull[m]))
        print(f"{m} done", flush=True)
    S = {"Our CHoCH plan": our_plan(Dfull), **S}
    res = {k: summarize(v) for k, v in S.items()}
    print(f"\n{'strategy':34} {'trades':>6} {'win':>5} {'Nov-Apr $':>10} {'May-Nov $':>10} {'year $':>9} {'Oct $':>8} {'+months':>8} {'worst dd $':>10}")
    for k, r in res.items():
        print(f"{k:34} {r['n']:6d} {r['win']:5.0%} {r['h1']:+10,.0f} {r['h2']:+10,.0f} {r['usd']:+9,.0f} {r['oct']:+8,.0f} {r['pos']:4d}/{r['months']:<3d} {r['dd']:+10,.0f}")
        print(f"{'':34} " + "  ".join(f"{m} {v:+,.0f}" for m, v in r["by_mkt"].items()))
    tpl = (ROOT / "tools" / "famous_strategies_template.html").read_text()
    style = (ROOT / "tools" / "plan_search_template.html").read_text()
    style = style[style.index("<style>"):style.index("</style>") + 8]
    out = ROOT / "reports" / "famous_strategies.html"
    out.write_text(tpl.replace("__STYLE__", style).replace("__DATA__", json.dumps(res)))
    print(out)
    if sys.platform == "darwin" and "--no-open" not in sys.argv:
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
