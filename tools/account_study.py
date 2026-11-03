"""The $1,000 -> $6,512 account test (Sep 29 - Oct 28, 2026), re-runnable.

Plan: 1M CHoCH signals (signals/engine.py, with the 15-candle "push into the level" rule) on MES, MNQ, MYM
and MGC, 8:30 AM - 2:00 PM CT. A+ any time, A and B only from 11:00. Markets are traded in groups that share
one account: by default the three indices are one group (first filled trade of the day across MES/MNQ/MYM)
and gold is another (its own first trade). Each trade risks 10% of the current balance in whole contracts
(capped by day margin), full exit at 2R, stop or 3:40 PM; $1.24 commission per contract, stops slip 1 tick.

    .venv/bin/python tools/account_study.py                 # cached data in data/, the tested plan
    .venv/bin/python tools/account_study.py --refresh       # add the latest Yahoo bars to the cache first
    .venv/bin/python tools/account_study.py --tiers A+      # A+ only
    .venv/bin/python tools/account_study.py --ab-after 0    # A and B any time

Yahoo keeps 1-minute bars for ~30 days. --refresh merges new bars into data/<SYM>_F_1m.csv instead of
replacing it, so the cache keeps growing. Results depend on the bars in the cache: the original run used
the cache as of Oct 28, 2026.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import signals.engine as E                                           # noqa: E402
from backtester.data import _clean, download_yahoo                   # noqa: E402

# market: (tick, $ per point, commission per contract round trip, day margin per contract, name)
MK = {"MES": (0.25, 5.0, 1.24, 50, "Micro S&P 500"),
      "MNQ": (0.25, 2.0, 1.24, 100, "Micro Nasdaq"),
      "MYM": (1.0, 0.5, 1.24, 50, "Micro Dow"),
      "MGC": (0.1, 10.0, 1.24, 100, "Micro Gold"),
      # tested Oct 2026 as possible extra markets; day margins are rough guesses, check your broker's
      "M2K": (0.1, 5.0, 1.24, 50, "Micro Russell 2000"),
      "SIL": (0.005, 1000.0, 1.24, 200, "Micro Silver"),
      "MCL": (0.01, 100.0, 1.24, 150, "Micro Crude Oil"),
      "MHG": (0.0005, 2500.0, 1.24, 150, "Micro Copper"),
      "MBT": (5.0, 0.1, 2.50, 1000, "Micro Bitcoin"),
      "M6E": (0.0001, 12500.0, 1.24, 100, "Micro Euro")}
NY = (8 * 60 + 30, 14 * 60)        # signal candle closes after 8:30 and by 2:00 PM CT


def load(m: str, refresh: bool) -> pd.DataFrame:
    cache = ROOT / "data" / f"{m}_F_1m.csv"
    old = None
    if cache.exists():
        old = pd.read_csv(cache, index_col=0)
        old.index = pd.to_datetime(old.index, utc=True)
        old = _clean(old)
    if refresh or old is None:
        print(f"Downloading {m}=F 1-minute bars...")
        new = download_yahoo(f"{m}=F")
        old = new if old is None else pd.concat([old, new])
        old = old[~old.index.duplicated(keep="last")].sort_index()
        cache.parent.mkdir(exist_ok=True)
        old.to_csv(cache)
    return old


def typical_range(d: pd.DataFrame) -> float:
    r = (d.high - d.low)[d.index.hour.isin(range(9, 15))]
    return float(r[r > 0].median())


# point scale vs MES, fixed to the Pine indicator's numbers (typical 1-minute candle size, Sep - Oct 2026) so the
# test and the indicator always use the same level distance and minimum stop; other markets: measured from the data
PINE_SCALE = {"MES": 1.0, "MNQ": 6.0, "MYM": 20 / 3, "MGC": 2 / 3, "SIL": 0.03 / 2.25, "M2K": 0.44}


def scale(D: dict, m: str) -> float:
    return PINE_SCALE.get(m) or typical_range(D[m]) / typical_range(D["MES"])


def find(D: dict, tiers: tuple, ab_after: float, approach: int) -> list[dict]:
    """Every filled-or-cancelled signal in the NY window, all markets. Point settings scale by candle size vs MES."""
    out = []
    for m, d in D.items():
        for s in E.find_signals(d, MK[m][0], scale(D, m), sessions=("NY",), approach=approach):
            mins = s["time"].hour * 60 + s["time"].minute
            if s["tier"] not in tiers or s["status"] in ("open", "waiting") or not NY[0] < mins <= NY[1]:
                continue
            if s["tier"] != "A+" and s["time"].hour + s["time"].minute / 60 < ab_after:
                continue
            out.append(dict(s, market=m))
    return out


def plan(sigs: list[dict], groups: list[tuple], max_day: int) -> list[dict]:
    """Inside each group: one order or trade at a time, at most max_day fills a day. Groups run side by side."""
    taken = []
    for g in groups:
        busy, fills = None, {}
        for s in sorted((x for x in sigs if x["market"] in g), key=lambda x: x["time"]):
            day = s["time"].date()
            if fills.get(day, 0) >= max_day or (busy is not None and s["time"] < busy):
                continue
            busy = s.get("exit_time")
            if s["r"] is not None:
                fills[day] = fills.get(day, 0) + 1
                taken.append(s)
    return taken


def half_at_1r(D: dict, s: dict) -> dict:
    """Re-follow a filled trade: at +1R close half (limit, no slippage) and move the rest's stop to the entry.
    Same candle order as the engine: stop first, then 1R, then target; 3:40 close at the open, 1 tick worse."""
    d, tick, side = D[s["market"]], MK[s["market"]][0], s["side"]
    o, h, l = (d[k].to_numpy() for k in ("open", "high", "low"))
    idx = d.index
    f = idx.get_loc(s["fill_time"])
    e = o[f] if ((o[f] < s["entry"]) if side == 1 else (o[f] > s["entry"])) else s["entry"]
    r1 = abs(e - s["stop"])
    one, tg = e + side * r1, e + side * E.TARGET_R * r1
    flat_t = s["time"].normalize() + pd.Timedelta(minutes=E.SESSIONS["NY"]["flat"])
    st, hit, hit_t = s["stop"], False, None
    for j in range(f, len(d)):
        if idx[j] >= flat_t:
            return dict(s, half_hit=True, half_time=hit_t, r_rest=(o[j] - side * tick - e) * side / r1, half_status="closed", half_exit=idx[j])
        if (l[j] <= st) if side == 1 else (h[j] >= st):
            gap = ((o[j] < st) if side == 1 else (o[j] > st)) and j != f
            px = (o[j] if gap else st) - side * tick
            if not hit:
                return dict(s, half_hit=False)                 # stopped before 1R: same as the plain trade
            return dict(s, half_hit=True, half_time=hit_t, r_rest=(px - e) * side / r1, half_status="breakeven", half_exit=idx[j])
        if not hit and ((h[j] >= one) if side == 1 else (l[j] <= one)):
            hit, st, hit_t = True, e, idx[j]
        if hit and ((h[j] >= tg) if side == 1 else (l[j] <= tg)):
            return dict(s, half_hit=True, half_time=hit_t, r_rest=E.TARGET_R, half_status="target", half_exit=idx[j])
    return dict(s, half_hit=False)


def account(trades: list[dict], start: float, pct: float) -> list[dict]:
    """Size each trade from the balance when it is placed; balance changes in exit order."""
    bal, rows = start, []
    for s in sorted(trades, key=lambda x: x["exit_time"]):
        tick, pv, comm, margin, _ = MK[s["market"]]
        per = s["risk_pts"] * pv
        n = min(math.floor(pct * bal / per), math.floor(bal / margin))
        if n < 1:
            rows.append(dict(s, n=0, usd=0.0, bal=bal, skipped=True))
            continue
        k = n // 2 if s.get("half_hit") else 0              # 1 contract can't be split: it runs the plain trade
        if not k:
            s = dict(s, half_time=None)
        if k:
            usd = (k * 1.0 + (n - k) * s["r_rest"]) * per - comm * n
            s = dict(s, r=(k * 1.0 + (n - k) * s["r_rest"]) / n, status=s["half_status"], exit_time=s["half_exit"])
        else:
            usd = s["r"] * per * n - comm * n
        bal += usd
        rows.append(dict(s, n=n, usd=usd, bal=bal, skipped=False))
    return rows


def chart_time(ts) -> int:
    """Seconds that read as Chicago clock time on the chart."""
    return int(pd.Timestamp(ts).tz_localize(None).timestamp())


def report(D: dict, rows: list[dict], a, groups: list[tuple], out: Path) -> None:
    """1-minute chart page: every trade with its push, CHoCH, fill, exit and the balance after it."""
    setups, nth = [], {}
    for k, s in enumerate(sorted(rows, key=lambda x: x["time"])):
        m, d = s["market"], D[s["market"]]
        tick, pv = MK[m][0], MK[m][1]
        h, l = d.high.to_numpy(), d.low.to_numpy()
        i, side = d.index.get_loc(s["time"] - pd.Timedelta(minutes=1)), s["side"]
        j = i - E.SWEEP + (int(np.argmin(l[i - E.SWEEP:i + 1])) if side == 1 else int(np.argmax(h[i - E.SWEEP:i + 1])))
        ext = float(l[j] if side == 1 else h[j])
        lv = E.levels(d)
        w = pd.DatetimeIndex(lv.when)
        near = lv[(lv.kind == 1) & (w <= d.index[j]) & (d.index[j] - w <= E.LOOK)
                  & ((lv.px - ext).abs() <= scale(D, m))]
        day = s["time"].strftime("%Y-%m-%d")
        if not s["skipped"]:
            nth[day] = nth.get(day, 0) + 1
        risked = s["n"] * s["risk_pts"] * pv
        status = "skipped" if s["skipped"] else s["status"] if s["status"] in ("target", "stop", "breakeven") else "closed"
        setups.append(dict(
            n=k + 1, market=m, desc=MK[m][4], tier=s["tier"], nth=nth.get(day, 1), day=day,
            time=s["time"].strftime("%a %b %d  %H:%M"), side="Long" if side == 1 else "Short",
            entry=s["entry"], stop=s["stop"], target=s["target"], risk=s["risk_pts"], strength=round(s["strength"], 2),
            status=status, outcome={"target": "target", "stop": "stop", "closed": "closed 3:40", "breakeven": "half at 1R, rest stopped at entry"}.get(status, status),
            r=round(s["usd"] / risked, 2) if risked else 0, usd=round(s["usd"], 2), whole=s["n"], risked=round(risked, 2),
            bal=round(s["bal"], 2), pv=pv, tick=tick, dec=0 if tick >= 1 else max(0, min(5, math.ceil(-math.log10(tick)))),
            ext=ext, level=float(near.px.iloc[(near.px - ext).abs().argmin()]) if len(near) else None,
            t_ext=chart_time(d.index[j]), t_choch=chart_time(d.index[i - 1]), t_sig=chart_time(d.index[i]),
            t_fill=chart_time(s["fill_time"]), t_exit=chart_time(s["exit_time"]),
            t_half=chart_time(s["half_time"]) if s.get("half_time") is not None else None,
            fill_at=f'{s["fill_time"]:%H:%M}', exit_at=f'{s["exit_time"]:%H:%M}',
            half_at=f'{s["half_time"]:%H:%M}' if s.get("half_time") is not None else None))
    bars = {}
    for m in {x["market"] for x in setups}:
        d = D[m]
        naive, keep = d.index.tz_localize(None), np.zeros(len(d), bool)
        for x in setups:
            if x["market"] == m:
                keep |= (naive >= pd.Timestamp(x["t_ext"] - 7200, unit="s")) & (naive <= pd.Timestamp(x["t_exit"] + 5400, unit="s"))
        bars[m] = [[chart_time(ts), r.open, r.high, r.low, r.close] for ts, r in zip(d.index[keep], d[keep].itertuples())]
    days = sorted({x.strftime("%Y-%m-%d") for x in D["MES"].index if x.weekday() < 5 and NY[0] <= x.hour * 60 + x.minute <= NY[1]})
    if a.start:
        days = [x for x in days if x >= a.start]
    traded = [x for x in setups if x["status"] != "skipped"]

    def summ(xs):
        return dict(n=len(xs), wins=int(sum(x["usd"] > 0 for x in xs)), usd=round(sum(x["usd"] for x in xs), 2),
                    days=len({x["day"] for x in xs}))
    tiers = a.tiers.replace(",", "/") + (f" (A/B only after {a.ab_after:g}:00)" if a.ab_after and a.tiers != "A+" else "")
    stats = dict(all=summ(traded), first=summ([x for x in traded if x["nth"] == 1]), total_days=len(days), target_r=E.TARGET_R,
                 risk=f"{a.risk_pct:g}% of balance", max_day=len(groups) * a.max_day, tiers=tiers + ", fixed rules",
                 markets=" + ".join("/".join(g) for g in groups) + f", {a.max_day} a day each",
                 by_mkt={m: summ([x for x in traded if x["market"] == m]) for g in groups for m in g},
                 start=a.start_balance, end=round(max(rows, key=lambda r: r["exit_time"])["bal"], 2), half=a.half_at_1r,
                 span=f"{pd.Timestamp(days[0]):%b %d} – {pd.Timestamp(days[-1]):%b %d, %Y}", other="", other_label="")
    html = (Path(__file__).with_name("account_study_template.html").read_text()
            .replace("__BARS__", json.dumps(bars)).replace("__SETUPS__", json.dumps(setups))
            .replace("__CAL__", json.dumps([dict(day=x) for x in days])).replace("__ST__", json.dumps(stats)))
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    print(f"\nChart page: {out}")


def main() -> None:
    p = argparse.ArgumentParser(description="Re-run the $1,000 account test of the CHoCH plan.")
    p.add_argument("--refresh", action="store_true", help="add the latest Yahoo bars to the cache first")
    p.add_argument("--tiers", default="A+,A,B", help="grades to take, e.g. A+ or A+,A")
    p.add_argument("--ab-after", type=float, default=11, help="A and B only from this hour CT (0 = any time)")
    p.add_argument("--groups", default="MES+MNQ+MYM,MGC", help="markets that share one first-trade-of-the-day")
    p.add_argument("--max-day", type=int, default=1, help="filled trades a day per group")
    p.add_argument("--start-balance", type=float, default=1000)
    p.add_argument("--risk-pct", type=float, default=10)
    p.add_argument("--approach", type=int, default=15, help="candles before the push that must not go past it (0 = off)")
    p.add_argument("--from", dest="start", help="first day, e.g. 2026-09-29")
    p.add_argument("--to", dest="end", help="last day, e.g. 2026-10-28")
    p.add_argument("--half-at-1r", action="store_true", help="close half at +1R and move the stop to the entry")
    p.add_argument("--out", default="account_study.html", help="chart page written to reports/")
    p.add_argument("--no-open", action="store_true", help="don't open the chart page in a browser")
    p.add_argument("--taken", help="only the plan trades the user really took: a journal CSV (market, date, signal_time)")
    a = p.parse_args()

    groups = [tuple(g.split("+")) for g in a.groups.split(",")]
    D = {}
    for m in sorted({m for g in groups for m in g} | {"MES"}):
        d = load(m, a.refresh)
        if a.start:
            d = d[d.index >= pd.Timestamp(a.start, tz=E.TZ) - pd.Timedelta(days=4)]   # earlier bars build the levels
        if a.end:
            d = d[d.index < pd.Timestamp(a.end, tz=E.TZ) + pd.Timedelta(days=1)]
        D[m] = d
    sigs = find(D, tuple(a.tiers.split(",")), a.ab_after, a.approach)
    if a.start:
        sigs = [s for s in sigs if s["time"] >= pd.Timestamp(a.start, tz=E.TZ)]
    taken = plan(sigs, groups, a.max_day)
    if a.taken:
        import csv
        mine = {(r["market"], r["date"], r["signal_time"]) for r in csv.DictReader(open(a.taken))}
        taken = [s for s in taken if (s["market"], f"{s['time']:%Y-%m-%d}", f"{s['time']:%H:%M}") in mine]
    if a.half_at_1r:
        taken = [half_at_1r(D, s) for s in taken]
    rows = account(taken, a.start_balance, a.risk_pct / 100)

    span = " - ".join(f"{D['MES'].index[k]:%b %d %H:%M}" for k in (0, -1))
    print(f"\nMES bars {span} · groups {' | '.join('/'.join(g) for g in groups)} · {a.tiers}"
          f"{f' (A/B from {a.ab_after:g}:00)' if a.ab_after else ''} · {a.risk_pct:g}% risk · 2R{' · half at 1R, stop to entry' if a.half_at_1r else ''}\n")
    print(f"{'#':>3}  {'signal (CT)':17} {'mkt':4} {'gr':2} {'side':5} {'entry':>10} {'stop':>10} {'target':>10}"
          f"  {'result':7} {'R':>6} {'ct':>3} {'P/L':>8} {'balance':>9}")
    for k, r in enumerate(sorted(rows, key=lambda x: x["time"]), 1):
        dec = 0 if MK[r["market"]][0] >= 1 else 2
        res = "skipped" if r["skipped"] else {"target": "target", "stop": "stop", "breakeven": "BE"}.get(r["status"], "3:40")
        print(f"{k:3d}  {r['time']:%a %b %d %H:%M} {r['market']:4} {r['tier']:2} {'Buy' if r['side'] == 1 else 'Sell':5}"
              f" {r['entry']:10.{dec}f} {r['stop']:10.{dec}f} {r['target']:10.{dec}f}  {res:7} {r['r']:+6.2f}"
              f" {r['n']:3d} {r['usd']:+8.0f} {r['bal']:9,.0f}")
    t = [r for r in rows if not r["skipped"]]
    eq = pd.Series([a.start_balance] + [r["bal"] for r in rows])
    print(f"\n${a.start_balance:,.0f} -> ${eq.iloc[-1]:,.0f} · {len(t)} trades, {sum(r['usd'] > 0 for r in t)} wins"
          f" · peak ${eq.max():,.0f} · worst drop {((eq - eq.cummax()) / eq.cummax()).min():.0%}")
    for m in MK:
        x = [r for r in t if r["market"] == m]
        if x:
            print(f"  {m}: {len(x)} trades, {sum(r['usd'] > 0 for r in x)} wins, {sum(r['usd'] for r in x):+,.0f}")
    out = ROOT / "reports" / a.out
    report(D, rows, a, groups, out)
    if not a.no_open and sys.platform == "darwin":
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
