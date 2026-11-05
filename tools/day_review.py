"""Review one trading day with the $6,512 plan: every signal on MES, MNQ, MYM, MGC and SIL, which ones the plan takes,
and what the day made at 10% of the balance. Opens a 1-minute chart page for each signal.

    .venv/bin/python tools/day_review.py                         # today, fresh Yahoo bars, $1,000 balance
    .venv/bin/python tools/day_review.py 2026-10-28 --no-refresh  # another day from the cached bars
    .venv/bin/python tools/day_review.py --balance 1350           # size from your real balance

The plan (same as tools/account_study.py): MES / MNQ / MYM share one trade a day - the first index signal whose
limit fills - and gold (MGC) and silver (SIL) each have their own. While a signal's limit is working or its trade is open, later signals in
the same group are skipped. A fill that is too big for the risk (1 contract > 10%) still ends that group's day.
Signals that are still working when the data ends are shown as open / waiting. Yahoo bars can lag by a few minutes.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import account_study as A                                             # noqa: E402
from account_study import E, MK, NY, ROOT, chart_time, scale  # noqa: E402

GROUPS = [("MES", "MNQ", "MYM"), ("MGC",), ("SIL",)]


def day_signals(D: dict, day: pd.Timestamp, ab_after: float) -> list[dict]:
    """Every valid signal on `day`, including ones still working at the end of the data."""
    out = []
    for m, d in D.items():
        for s in E.find_signals(d, MK[m][0], scale(D, m), sessions=("NY",), approach=15):
            mins = s["time"].hour * 60 + s["time"].minute
            if s["time"].date() != day.date() or not NY[0] < mins <= NY[1]:
                continue
            if s["tier"] != "A+" and s["time"].hour + s["time"].minute / 60 < ab_after:
                continue
            out.append(dict(s, market=m))
    return sorted(out, key=lambda x: (x["time"], list(MK).index(x["market"])))


def take(sigs: list[dict], balance: float, pct: float) -> None:
    """Mark each signal taken / not taken by the plan, size it from the balance at its fill, add the result."""
    busy, closed = {}, []
    for s in sigs:
        g = next(k for k, grp in enumerate(GROUPS) if s["market"] in grp)
        s["group"] = g
        tick, pv, comm, margin, _ = MK[s["market"]]
        per = s["risk_pts"] * pv
        bal = balance + sum(u for t, u in closed if s.get("fill_time") is not None and t <= s["fill_time"])
        s["n"] = min(math.floor(pct * bal / per), math.floor(bal / margin))
        s["per"] = per
        first = next((x for x in sigs if x.get("taken") and x["group"] == g), None)
        if first is not None and first.get("fill_time") is not None:
            s["taken"], s["why"] = False, f"not taken: {first['market']} already filled today"
            continue
        if busy.get(g) is not None and s["time"] < busy[g][0]:
            s["taken"], s["why"] = False, f"not taken: {busy[g][1]} limit / trade still working"
            continue
        end = s.get("exit_time")
        busy[g] = (end if end is not None else pd.Timestamp("2200-01-01", tz=E.TZ), s["market"])
        if s["status"] in ("no fill", "waiting"):
            s["taken"] = None                    # an order you place, but nothing happens unless it fills
            s["why"] = "limit never filled (cancelled)" if s["status"] == "no fill" else "limit still working"
            continue
        s["taken"] = True
        if s["n"] < 1:
            s["why"] = f"SKIP: 1 contract risks ${per:,.0f}, more than {pct:.0%} (${pct * bal:,.0f}) - still ends the day"
            s["usd"] = 0.0
            continue
        s["usd"] = s["r"] * per * s["n"] - comm * s["n"]
        s["why"] = "TAKEN" + (" (still open)" if s["status"] == "open" else "")
        if s["status"] != "open":
            closed.append((s["exit_time"], s["usd"]))


def page(D: dict, sigs: list[dict], day: pd.Timestamp, balance: float, pct: float, out: Path) -> None:
    setups = []
    for k, s in enumerate(sigs):
        m, d = s["market"], D[s["market"]]
        tick, pv = MK[m][0], MK[m][1]
        h, l = d.high.to_numpy(), d.low.to_numpy()
        i, side = d.index.get_loc(s["time"] - pd.Timedelta(minutes=1)), s["side"]
        j = i - E.SWEEP + (int(np.argmin(l[i - E.SWEEP:i + 1])) if side == 1 else int(np.argmax(h[i - E.SWEEP:i + 1])))
        ext = float(l[j] if side == 1 else h[j])
        lv = E.levels(d)
        w = pd.DatetimeIndex(lv.when)
        near = lv[(lv.kind == 1) & (w <= d.index[j]) & (d.index[j] - w <= E.LOOK) & ((lv.px - ext).abs() <= scale(D, m))]
        res = {"target": "target hit", "stop": "stopped", "closed": "closed 3:40", "no fill": "never filled",
               "open": "still open", "waiting": "limit working"}[s["status"]]
        r = s.get("r")
        usd = s.get("usd", 0.0) if s.get("taken") else 0.0
        note = (f"{s['why']} · {res}" + (f" · {r:+.2f}R" if r is not None else "")
                + (f" = {'+' if usd >= 0 else '-'}${abs(usd):,.0f} on {s['n']} contracts" if s.get("taken") and s["n"] >= 1 else ""))
        exit_t = s.get("exit_time") if s.get("exit_time") is not None else d.index[-1]
        setups.append(dict(
            n=k + 1, market=m, desc=MK[m][4], tier=s["tier"], nth=1 if s.get("taken") else 2, day=day.strftime("%Y-%m-%d"),
            time=s["time"].strftime("%a %b %d  %H:%M"), side="Long" if side == 1 else "Short",
            entry=s["entry"], stop=s["stop"], target=s["target"], risk=s["risk_pts"], strength=round(s["strength"], 2),
            status=s["status"] if s["status"] in ("target", "stop") else "closed", outcome=f"{s['why']} · {res}",
            note=note, exit_text={"no fill": "Cancelled", "open": "Now (open)", "waiting": "Now (working)"}.get(s["status"]),
            r=round(r, 2) if r is not None else 0, usd=round(usd, 2), whole=max(s["n"], 0), risked=round(max(s["n"], 0) * s["per"], 2),
            bal=None, pv=pv, tick=tick, dec=0 if tick >= 1 else max(0, min(5, math.ceil(-math.log10(tick)))),
            ext=ext, level=float(near.px.iloc[(near.px - ext).abs().argmin()]) if len(near) else None,
            t_ext=chart_time(d.index[j]), t_choch=chart_time(d.index[i - 1]), t_sig=chart_time(d.index[i]),
            t_fill=chart_time(s["fill_time"]) if s.get("fill_time") is not None else None, t_exit=chart_time(exit_t),
            fill_at=f'{s["fill_time"]:%H:%M}' if s.get("fill_time") is not None else None,
            exit_at=f'{s["exit_time"]:%H:%M}' if s.get("fill_time") is not None and s.get("exit_time") is not None
            and s["status"] != "open" else None))
    bars = {}
    for m in {x["market"] for x in setups}:
        d = D[m]
        naive = d.index.tz_localize(None)
        keep = (naive >= pd.Timestamp(day.date()) + pd.Timedelta(hours=6)) & (naive <= pd.Timestamp(day.date()) + pd.Timedelta(hours=16))
        bars[m] = [[chart_time(ts), r.open, r.high, r.low, r.close] for ts, r in zip(d.index[keep], d[keep].itertuples())]
    taken = [x for x in setups if x["nth"] == 1]
    summ = lambda xs: dict(n=len(xs), wins=int(sum(x["usd"] > 0 for x in xs)), usd=round(sum(x["usd"] for x in xs), 2), days=1 if xs else 0)
    stats = dict(all=summ(taken), first=summ(taken), total_days=1, target_r=E.TARGET_R, risk=f"{pct:.0%} of balance", max_day=2,
                 tiers="A+ any time, A/B from 11:00", markets=f"{day:%a %b %d}: every signal, plan's trades marked",
                 by_mkt={m: summ([x for x in taken if x["market"] == m]) for g in GROUPS for m in g}, start=balance,
                 end=round(balance + sum(x["usd"] for x in taken), 2), half=False, span=f"{day:%b %d, %Y}", other="", other_label="",
                 fo_label="only the trades the plan takes", pad=60)
    html = ((Path(__file__).with_name("account_study_template.html")).read_text()
            .replace("__BARS__", json.dumps(bars)).replace("__SETUPS__", json.dumps(setups))
            .replace("__CAL__", json.dumps([dict(day=day.strftime("%Y-%m-%d"))])).replace("__ST__", json.dumps(stats)))
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)


def main() -> None:
    p = argparse.ArgumentParser(description="Review a day with the $6,512 plan.")
    p.add_argument("day", nargs="?", help="YYYY-MM-DD (default: today, Chicago time)")
    p.add_argument("--balance", type=float, default=1000, help="your balance at the start of the day")
    p.add_argument("--risk-pct", type=float, default=10)
    p.add_argument("--ab-after", type=float, default=11)
    p.add_argument("--no-refresh", action="store_true", help="use the cached bars only")
    p.add_argument("--no-open", action="store_true")
    a = p.parse_args()

    day = pd.Timestamp(a.day or pd.Timestamp.now(tz=E.TZ).strftime("%Y-%m-%d"), tz=E.TZ)
    D = {m: A.load(m, not a.no_refresh) for g in GROUPS for m in g}
    D = {m: d[d.index < day + pd.Timedelta(days=1)] for m, d in D.items()}
    sigs = day_signals(D, day, a.ab_after)
    take(sigs, a.balance, a.risk_pct / 100)

    last = min(d.index[-1] for d in D.values())
    print(f"\n{day:%a %b %d, %Y} · bars up to {last:%H:%M} CT · balance ${a.balance:,.0f} · {a.risk_pct:g}% risk\n")
    for s in sigs:
        dec = 0 if MK[s["market"]][0] >= 1 else 2
        r = s.get("r")
        usd = f"{s['usd']:+,.0f}" if s.get("taken") and s["n"] >= 1 else ""
        print(f"  {s['time']:%H:%M}  {s['market']}  {s['tier']:2}  {'Buy ' if s['side'] == 1 else 'Sell'}  entry {s['entry']:.{dec}f}"
              f"  stop {s['stop']:.{dec}f}  target {s['target']:.{dec}f}  {s['status']:8} {'' if r is None else f'{r:+.2f}R':7} {usd:>7}  {s['why']}")
    taken = [s for s in sigs if s.get("taken") and s["n"] >= 1]
    total = sum(s["usd"] for s in taken)
    if not sigs:
        print("  no signals")
    print(f"\nPlan's result: {len(taken)} trade{'s' if len(taken) != 1 else ''}, {total:+,.0f} -> balance ${a.balance + total:,.0f}")
    out = ROOT / "reports" / f"day_{day:%Y-%m-%d}.html"
    page(D, sigs, day, a.balance, a.risk_pct / 100, out)
    print(f"Chart page: {out}")
    if not a.no_open and sys.platform == "darwin":
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
