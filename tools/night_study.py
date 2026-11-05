"""Night trading test: ONE A+ CHoCH trade a night, same rules as the day plan.

Night windows (signal candle close, CT): Asia 7:00 - 11:00 PM (open trades closed 2:00 AM) and
London 2:00 - 5:00 AM (closed 7:00 AM). A "night" is one CME session (5 PM - next 5 PM), so an
Asia trade at 8 PM and a London trade at 3 AM belong to the same night. The first A+ signal that fills
(across the chosen markets) is the night's trade; signals while an order is working are skipped.
15% of balance, 2R target, half at +1R with the stop to entry (2+ contracts), $1.24/contract, stops slip 1 tick.

    .venv/bin/python tools/night_study.py                       # every window/market variant, chart of the best
    .venv/bin/python tools/night_study.py --windows Asia --markets MES,MNQ,MYM
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import account_study as A                                            # noqa: E402
import signals.engine as E                                           # noqa: E402

ALL = ("MES", "MNQ", "MYM", "MGC", "SIL")


def night_signals(D: dict, windows: tuple, markets: tuple) -> list[dict]:
    out = []
    for m in markets:
        for s in E.find_signals(D[m], A.MK[m][0], A.scale(D, m), sessions=windows, approach=15):
            if s["tier"] == "A+" and s["status"] not in ("open", "waiting"):
                out.append(dict(s, market=m, night=E.session_date(pd.DatetimeIndex([s["time"]]))[0]))
    return out


def one_a_night(sigs: list[dict]) -> list[dict]:
    taken, busy, done = [], None, set()
    for s in sorted(sigs, key=lambda x: x["time"]):
        if s["night"] in done or (busy is not None and s["time"] < busy):
            continue
        busy = s.get("exit_time")
        if s["r"] is not None:
            done.add(s["night"])
            taken.append(s)
    return taken


def half(D: dict, s: dict) -> dict:
    """account_study.half_at_1r, but closing at the night window's flat time instead of 3:40 PM."""
    sess = E.SESSIONS[s["session"]]
    flat = s["time"].normalize() + pd.Timedelta(minutes=sess["flat"])
    if sess["start"] > sess["end"] and s["time"].hour * 60 + s["time"].minute <= sess["end"]:
        flat -= pd.Timedelta(days=1)
    saved = E.SESSIONS["NY"]
    E.SESSIONS["NY"] = dict(saved, flat=int((flat - s["time"].normalize()).total_seconds() // 60))
    try:
        return A.half_at_1r(D, s)
    finally:
        E.SESSIONS["NY"] = saved


def day_plan(D, first):
    """The day plan as traded: MES/MNQ/MYM, MGC and SIL each one fill a day, A+ any time, A/B from 11:00."""
    sigs = [s for s in A.find(D, ("A+", "A", "B"), 11, 15) if s["time"] >= first]
    return A.plan(sigs, [("MES", "MNQ", "MYM"), ("MGC",), ("SIL",)], 1)


def run(D, windows, markets, start, pct, first, day=()):
    night = one_a_night([s for s in night_signals(D, windows, markets) if s["time"] >= first]) if windows else []
    plain = night + list(day)
    taken = [half(D, s) if s["session"] != "NY" else A.half_at_1r(D, s) for s in plain]
    rows = A.account(taken, start, pct)
    eq = pd.Series([start] + [r["bal"] for r in rows])
    t = [r for r in rows if not r["skipped"]]
    R = sum(r["r"] for r in t)
    return rows, dict(n=len(t), wins=sum(r["usd"] > 0 for r in t), R=R, end=eq.iloc[-1],
                      dd=((eq - eq.cummax()) / eq.cummax()).min(),
                      raw_R=sum(s["r"] for s in plain if (s["market"], s["time"]) in {(r["market"], r["time"]) for r in t}))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--windows", help="Asia, London or Asia,London (default: test all)")
    p.add_argument("--markets", help="comma list (default: test all)")
    p.add_argument("--start-balance", type=float, default=1000)
    p.add_argument("--risk-pct", type=float, default=15)
    p.add_argument("--from", dest="start", default="2026-09-30", help="first day (the cache starts Sep 30)")
    p.add_argument("--with-day", action="store_true", help="add the day plan's trades to the same account")
    p.add_argument("--out", default="night_study.html")
    p.add_argument("--no-open", action="store_true")
    a = p.parse_args()
    D = {m: A.load(m, False) for m in ALL}
    first = pd.Timestamp(a.start, tz=E.TZ)
    last = min(d.index[-1] for d in D.values())
    print(f"Bars {first:%b %d} - {last:%b %d %H:%M} CT · A+ only · 1 trade a night · {a.risk_pct:g}% · half at +1R\n")
    wins = [tuple(a.windows.split(","))] if a.windows else [("Asia",), ("London",), ("Asia", "London")]
    mkts = [tuple(a.markets.split(","))] if a.markets else [ALL, ("MES", "MNQ", "MYM"), ("MGC", "SIL")]
    print(f"{'window':14} {'markets':22} {'trades':>6} {'wins':>5} {'R (plain 2R)':>13} {'R (half 1R)':>12} {'$1,000 ->':>10} {'worst drop':>11}")
    best, D2 = None, {k: v[v.index >= first - pd.Timedelta(days=4)] for k, v in D.items()}
    day = day_plan(D2, first) if a.with_day else ()
    if a.with_day:
        print("Day plan (8:30 - 2:00) + the night trade, one account. First row: day plan alone.")
        wins = [()] + wins
    pick = ((tuple(a.windows.split(",")) if a.windows else ("Asia",)),
            (tuple(a.markets.split(",")) if a.markets else ("MES", "MNQ", "MYM"))) if a.with_day else (("Asia", "London"), ALL)
    for w in wins:
        for m in (mkts if w else [()]):
            rows, st = run(D2, w, m, a.start_balance, a.risk_pct / 100, first, day)
            print(f"{'+'.join(w) or 'day only':14} {'/'.join(m):22} {st['n']:6d} {st['wins']:5d} {st['raw_R']:+13.1f} {st['R']:+12.1f}"
                  f" {st['end']:10,.0f} {st['dd']:11.0%}")
            if best is None or (w, m) == pick:                           # the charted variant
                best = (w, m, rows)
    w, m, rows = best
    print(f"\nTrades, {'+'.join(w)}, {'/'.join(m)}:")
    for k, r in enumerate(sorted(rows, key=lambda x: x["time"]), 1):
        res = "skipped" if r["skipped"] else {"target": "target", "stop": "stop", "breakeven": "BE"}.get(r["status"], "flat")
        print(f"{k:3d}  {r['time']:%a %b %d %H:%M} {r['session']:6} {r['market']:4} {'Buy' if r['side'] == 1 else 'Sell':5}"
              f" {res:7} {r['r']:+6.2f} {r['n']:3d} {r['usd']:+8.0f} {r['bal']:9,.0f}")
    ns = SimpleNamespace(start=None, tiers="A+", ab_after=0, risk_pct=a.risk_pct, max_day=1, start_balance=a.start_balance,
                         half_at_1r=True)
    out = ROOT / "reports" / a.out
    A.report(D2, rows, ns, [ALL] if a.with_day else [m], out)
    out.write_text(out.read_text().replace("closed 3:40", "closed at flat time (3:40 PM day, 2 AM / 7 AM night)"))
    if not a.no_open and sys.platform == "darwin":
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
