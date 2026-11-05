"""Replay of the CHoCH bot (bot/CHoCH_Bot.pine) over the cached 1-minute bars: the orders it would have sent and
the account from $1,000. Same rules as the tested plan (tools/account_study.py): indices MES/MNQ/MYM share one
first fill a day (MES first, then MNQ, MYM on the same minute), gold and silver each their own; A+ any time,
A/B from 11:00 CT; 15% of the balance in whole contracts (skip if 1 contract is more - a skipped fill still ends
the slot's day); limit cancelled after 10 candles; 2+ contracts close half at +1R and move the stop to the
entry; 2R target; flat at 3:40 PM.

Two pages, linked to each other:
  touch    - a limit fills when price touches it (as tested)
  through  - price must trade 1 tick past the limit (no fill on a touch), closer to a real limit order

    .venv/bin/python bot/replay.py                     # Sep 30 - Oct 30, opens the touch page
    .venv/bin/python bot/replay.py --to 2026-11-02     # any range in data/
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import account_study as A                                            # noqa: E402
import signals.engine as E                                           # noqa: E402

GROUPS = [("MES", "MNQ", "MYM"), ("MGC",), ("SIL",)]
SLOT = {"MES": "indices", "MNQ": "indices", "MYM": "indices", "MGC": "gold", "SIL": "silver"}


def orders(sigs: list[dict], rows: list[dict]) -> list[dict]:
    """Every message the bot sends, in time order: entry, cancel, half + move stop, flat at 3:40.
    Stops and targets need no message - they sit at the broker as the entry's bracket."""
    by_key = {(r["market"], r["time"]): r for r in rows}
    out = []
    for g in GROUPS:
        busy, filled = None, set()
        for s in sorted((x for x in sigs if x["market"] in g), key=lambda x: x["time"]):
            day = s["time"].date()
            if day in filled or (busy is not None and s["time"] < busy):
                continue
            busy = s.get("exit_time")
            r = by_key.get((s["market"], s["time"]))
            n = r["n"] if r else "15% of balance"
            base = dict(slot=SLOT[s["market"]], ticker=f"{s['market']}1!", grade=s["tier"])
            if s["r"] is not None:
                filled.add(day)
            if r is None:                       # never filled: placed, then cancelled
                out.append(dict(base, time=s["time"], action="buy" if s["side"] == 1 else "sell", orderType="limit",
                                limitPrice=s["entry"], stopLoss=s["stop"], takeProfit=s["target"], quantity=n))
                out.append(dict(base, time=s["exit_time"], action="cancel", note="limit not filled after 10 candles"))
                continue
            if r["skipped"]:
                out.append(dict(base, time=s["time"], action="none",
                                note="SKIP: 1 contract risks more than 15% - no order, slot still used if it fills"))
                continue
            out.append(dict(base, time=s["time"], action="buy" if s["side"] == 1 else "sell", orderType="limit",
                            quantity=n, limitPrice=s["entry"], stopLoss=s["stop"], takeProfit=s["target"]))
            k = n // 2 if r.get("half_time") is not None else 0
            if k:
                out.append(dict(base, time=r["half_time"], action="exit", quantity=k, note="+1R: close half"))
                out.append(dict(base, time=r["half_time"], action="move_stop", stopLoss=r["entry"], note="stop to entry"))
            if r["status"] == "closed":
                out.append(dict(base, time=r["exit_time"], action="exit_all", note="3:40 PM flat"))
    return sorted(out, key=lambda x: x["time"])


def run(D: dict, a, through: int, out: str, other: str, other_label: str) -> dict:
    E.FILL_THROUGH = through
    sigs = [s for s in A.find(D, ("A+", "A", "B"), 11, 15) if not a.start or s["time"] >= A.pd.Timestamp(a.start, tz=E.TZ)]
    taken = [A.half_at_1r(D, s) for s in A.plan(sigs, GROUPS, 1)]
    rows = A.account(taken, a.start_balance, a.risk_pct / 100)
    a.tiers, a.ab_after, a.half_at_1r, a.max_day = "A+,A,B", 11, True, 1
    A.report(D, rows, a, GROUPS, ROOT / "reports" / out)
    page = ROOT / "reports" / out
    page.write_text(page.read_text().replace('"other": "", "other_label": ""',
                                             f'"other": "{other}", "other_label": "{other_label}"', 1))
    log = orders(sigs, rows)
    end = max(rows, key=lambda r: r["exit_time"])["bal"] if rows else a.start_balance
    bals = [a.start_balance] + [r["bal"] for r in sorted(rows, key=lambda r: r["exit_time"])]
    peak, dd = bals[0], 0.0
    for b in bals:
        peak = max(peak, b)
        dd = min(dd, b / peak - 1)
    return dict(end=end, trades=sum(not r["skipped"] for r in rows), wins=sum(r["usd"] > 0 for r in rows), dd=dd, log=log)


def main() -> None:
    p = argparse.ArgumentParser(description="Replay the CHoCH bot over the cached bars.")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end", default="2026-10-30")
    p.add_argument("--start-balance", type=float, default=1000)
    p.add_argument("--risk-pct", type=float, default=15)
    p.add_argument("--no-open", action="store_true")
    a = p.parse_args()
    a.approach = 15

    D = {}
    for m in ("MES", "MGC", "MNQ", "MYM", "SIL"):                    # same order as the test (ties: MES, MNQ, MYM)
        d = A.load(m, False)
        if a.start:
            d = d[d.index >= A.pd.Timestamp(a.start, tz=E.TZ) - A.pd.Timedelta(days=4)]
        if a.end:
            d = d[d.index < A.pd.Timestamp(a.end, tz=E.TZ) + A.pd.Timedelta(days=1)]
        D[m] = d

    res = {}
    for name, through, label in (("touch", 0, "fill only if price trades 1 tick through the limit"),
                                 ("through", 1, "fill on a touch (as tested)")):
        other = "bot_replay_through.html" if name == "touch" else "bot_replay_touch.html"
        res[name] = run(D, a, through, f"bot_replay_{name}.html", other, label)
    E.FILL_THROUGH = 0

    log_path = ROOT / "bot" / "orders_replay.jsonl"
    with log_path.open("w") as f:
        for o in res["touch"]["log"]:
            f.write(json.dumps({k: (f"{v:%Y-%m-%d %H:%M}" if k == "time" else round(v, 6) if isinstance(v, float) else v) for k, v in o.items()}) + "\n")

    print()
    for name, r in res.items():
        print(f"{name:8} ${a.start_balance:,.0f} -> ${r['end']:,.0f} · {r['trades']} trades, {r['wins']} wins · worst drop {r['dd']:.0%}")
    print(f"\nOrder messages the bot would have sent (touch fills): {log_path}  ({len(res['touch']['log'])} messages)")
    if not a.no_open:
        subprocess.run(["open", str(ROOT / "reports" / "bot_replay_touch.html")])


if __name__ == "__main__":
    main()
