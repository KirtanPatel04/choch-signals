"""The live plan day by day from $1,000: day 1 = Sep 30 (first day with data) up to today, and today on its own
as a fresh day 1 (every signal and what the plan did with it). Writes reports/plan_by_day.html.

    .venv/bin/python tools/plan_by_day.py            # cached bars (refresh them first with daily_update.sh)
"""
from __future__ import annotations

import html
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import account_study as A                                            # noqa: E402
import signals.engine as E                                           # noqa: E402

GROUPS = [("MES", "MNQ", "MYM"), ("MGC",), ("SIL",)]
START, PCT = 1000.0, 0.15
OUT = A.ROOT / "reports" / "plan_by_day.html"


def run(D: dict, sigs: list[dict]) -> list[dict]:
    taken = [A.half_at_1r(D, s) for s in A.plan(sigs, GROUPS, 1)]
    return A.account(taken, START, PCT)


def money(x: float) -> str:
    return ("+" if x > 0 else "−" if x < 0 else "") + f"${abs(x):,.0f}"


def hm(t) -> str:
    return "" if t is None or pd.isna(t) else t.strftime("%-I:%M %p")


def result(r: dict) -> str:
    if r["skipped"]:
        return "skipped (1 contract > 15%)"
    return {"target": "target hit", "stop": "stopped", "breakeven": "half at +1R, rest at entry",
            "closed": "closed 3:40 PM"}.get(r["status"], r["status"])


def main() -> None:
    D = {m: A.load(m, False) for m in ("MES", "MGC", "MNQ", "MYM", "SIL")}
    sigs = A.find(D, ("A+", "A", "B"), 11, 15)
    rows = sorted(run(D, sigs), key=lambda r: r["exit_time"])
    days = sorted({t.date() for t in D["MES"].index if t.weekday() < 5 and A.NY[0] <= t.hour * 60 + t.minute <= A.NY[1]})
    today = days[-1]
    last_bar = D["MES"].index[-1]

    # every day since Sep 30
    bal, peak, worst, body = START, START, 0.0, []
    for n, day in enumerate(days, 1):
        xs = [r for r in rows if r["exit_time"].date() == day]
        start_bal = bal
        for r in xs:
            bal = r["bal"]
            peak = max(peak, bal)
            worst = min(worst, bal / peak - 1)
        pl = bal - start_bal
        trades = "<br>".join(
            f"{r['market']} {r['tier']} {'buy' if r['side'] == 1 else 'sell'} {hm(r['fill_time']) if r.get('fill_time') is not None else hm(r['time'])}"
            f" → {result(r)} {money(r['usd'])}" for r in sorted(xs, key=lambda r: r["time"])) or "<span class=m>no trade</span>"
        cls = "up" if pl > 0 else "dn" if pl < 0 else ""
        body.append(f"<tr class='{'today' if day == today else ''}'><td>{n}</td><td>{day:%a %b %-d}</td><td>{trades}</td>"
                    f"<td class='{cls} num'>{money(pl) if xs else '—'}</td><td class=num>${bal:,.0f}</td></tr>")

    # today on its own, as a fresh $1,000 day 1
    day_sigs = [s for s in sigs if s["time"].date() == today]
    t_rows = {(r["market"], r["time"]): r for r in run(D, day_sigs)}
    plan_keys = {(s["market"], s["time"]) for s in A.plan(day_sigs, GROUPS, 1)}
    t_body, t_bal = [], START
    for s in sorted(day_sigs, key=lambda s: s["time"]):
        r = t_rows.get((s["market"], s["time"]))
        per = s["risk_pts"] * A.MK[s["market"]][1]
        n = int(min(PCT * START // per, START // A.MK[s["market"]][3]))
        if r:
            what, usd = result(r), r["usd"]
            t_bal += usd
        elif (s["market"], s["time"]) in plan_keys or s["status"] == "no fill":
            what, usd = "limit never filled", 0.0
        else:
            what, usd = "not taken (slot busy / done)", 0.0
        size = f"{n} contracts" if n >= 1 else f"skip (1 contract = ${per:,.0f})"
        t_body.append(f"<tr><td>{hm(s['time'])}</td><td>{s['market']}</td><td>{s['tier']}</td>"
                      f"<td>{'Buy' if s['side'] == 1 else 'Sell'} {s['entry']:g}</td><td>{s['stop']:g}</td><td>{s['target']:g}</td>"
                      f"<td>{size}</td><td>{html.escape(what)}</td><td class=num>{money(usd) if r else '—'}</td></tr>")
    t_table = "".join(t_body) or "<tr><td colspan=9 class=m>No setups today.</td></tr>"

    OUT.write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Plan by Day</title>
<style>
:root{{--bg:#0f1117;--panel:#171a23;--line:#262b38;--text:#e6e8ee;--muted:#8a91a3;--win:#26a69a;--loss:#ef5350;--hi:#4f8cff}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:20px 16px}}
main{{max-width:1000px;margin:0 auto}} h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}}
.m{{color:var(--muted)}} .cards{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 16px;min-width:150px}}
.card b{{display:block;font-size:20px;font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border-radius:8px;overflow:hidden}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{color:var(--muted);font-weight:500}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}} .up{{color:var(--win)}} .dn{{color:var(--loss)}}
tr.today td{{background:#1d2333}} tr.today td:first-child{{box-shadow:inset 3px 0 var(--hi)}}
.wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>The plan from $1,000, day by day</h1>
<div class=m>Indices (MES/MNQ/MYM share the first fill) + gold + silver · A+ any time, A/B from 11:00 · 15% risk ·
half at +1R, stop to entry · 2R · flat 3:40 PM · Omaha (Central) time · Yahoo 1-minute bars up to {last_bar:%b %-d %-I:%M %p}</div>

<h2>Today ({today:%a %b %-d}) as day 1, starting with $1,000</h2>
<div class=cards><div class=card><span class=m>Start</span><b>$1,000</b></div>
<div class=card><span class=m>End of today</span><b>${t_bal:,.0f}</b></div>
<div class=card><span class=m>Today</span><b class="{'up' if t_bal > START else 'dn' if t_bal < START else ''}">{money(t_bal - START) if t_bal != START else '$0'}</b></div></div>
<div class=wrap><table><tr><th>Signal</th><th>Market</th><th>Grade</th><th>Limit</th><th>Stop</th><th>Target</th><th>Size at $1,000</th><th>What happened</th><th class=num>P/L</th></tr>
{t_table}</table></div>

<h2>If you had started on Sep 30 with $1,000</h2>
<div class=cards><div class=card><span class=m>Start (Sep 30)</span><b>$1,000</b></div>
<div class=card><span class=m>Now ({today:%b %-d})</span><b class=up>${bal:,.0f}</b></div>
<div class=card><span class=m>Trades</span><b>{sum(not r['skipped'] for r in rows)}</b></div>
<div class=card><span class=m>Worst drop</span><b class=dn>{worst:.0%}</b></div></div>
<div class=wrap><table><tr><th>Day</th><th>Date</th><th>Trades (fill time → result)</th><th class=num>Day P/L</th><th class=num>Balance</th></tr>
{''.join(body)}</table></div>
<p class=m>A backtest on Yahoo's bars, not your account. Limits count as filled on a touch; one month of data.</p>
</main></body></html>""")
    print(f"day 1 = {days[0]}, day {len(days)} = {today}: $1,000 -> ${bal:,.0f}; today alone: ${t_bal:,.0f}\n{OUT}")
    subprocess.run(["open", str(OUT)])


if __name__ == "__main__":
    main()
