"""Full-year search for a CHoCH plan like ours that makes money in every part of the year, not just October.

Same signals as the Pine (signals/engine.py) on the Databento year in data_long/ (tools/import_databento.py).
Varied, one at a time and together: grades, the A/B cutoff hour, the entry window, which markets share a daily
slot, trades a day per slot, target (1.5R / 2R / 3R) and half at +1R. Every variant is scored in R per trade
after commission (1 contract's commission / its $ risk), so account size doesn't decide the winner.

Honest test: variants are ranked on the FIRST half (Nov 3 2025 - Apr 30 2026) and then shown on the SECOND half
(May 1 - Nov 2 2026), which the ranking never saw. A variant only counts if it also makes money there.

    .venv/bin/python tools/plan_search.py          # writes reports/plan_search.html and opens it
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import account_study as A                                            # noqa: E402
import signals.engine as E                                           # noqa: E402

A.DATA = ROOT / "data_long"
FIRST, SPLIT, LAST = (pd.Timestamp(x, tz=E.TZ) for x in ("2025-11-03", "2026-05-01", "2026-11-03"))   # LAST: Databento's end
MKTS = ("MES", "MNQ", "MYM", "MGC", "SIL")
GROUPS = {"indices 1 slot + MGC + SIL (ours)": [("MES", "MNQ", "MYM"), ("MGC",), ("SIL",)],
          "indices 1 slot + MGC": [("MES", "MNQ", "MYM"), ("MGC",)],
          "indices 1 slot": [("MES", "MNQ", "MYM")],
          "all 5 share 1 slot": [MKTS],
          "each market own slot": [(m,) for m in MKTS],
          "MYM only": [("MYM",)],
          "MYM + MGC": [("MYM",), ("MGC",)]}
TIERS = {"A+/A/B": ("A+", "A", "B"), "A+ only": ("A+",), "A+/A": ("A+", "A"), "A/B": ("A", "B"), "B only": ("B",)}
AB_AFTER = (11, 0)
START = (8 * 60 + 30, 9 * 60, 9 * 60 + 30, 10 * 60)
END = (11 * 60, 12 * 60, 14 * 60)
MAXDAY = (1, 2)
TARGETS = (2.0, 1.5, 3.0)
HALF = (True, False)
OURS = dict(groups="indices 1 slot + MGC + SIL (ours)", tiers="A+/A/B", ab=11, start=510, end=840, maxday=1, target=2.0, half=True)


def signals(D: dict, target: float) -> list[dict]:
    """Every filled-or-cancelled NY signal, with plain R, half-at-1R R and commission in R."""
    E.TARGET_R = target
    out = []
    for s in A.find(D, ("A+", "A", "B"), 0, 15):
        if not FIRST <= s["time"] < LAST:
            continue
        mins = s["time"].hour * 60 + s["time"].minute
        x = dict(market=s["market"], time=s["time"], tier=s["tier"], side=s["side"], mins=mins, exit_time=s["exit_time"],
                 filled=s["r"] is not None, r=s["r"], r_half=None, cost=0.0)
        if x["filled"]:
            h = A.half_at_1r(D, s)
            x["r_half"] = (1.0 + h["r_rest"]) / 2 if h.get("half_hit") else s["r"]
            x["cost"] = A.MK[s["market"]][2] / (s["risk_pts"] * A.MK[s["market"]][1])
        out.append(x)
    E.TARGET_R = 2.0
    return out


def plan(sigs: list[dict], groups, tiers, ab, start, end, maxday) -> list[dict]:
    """Same rule as account_study.plan: inside a group one order/trade at a time, maxday fills a day."""
    taken = []
    for g in groups:
        busy, fills = None, {}
        for s in sigs:
            if s["market"] not in g or s["tier"] not in tiers or not start < s["mins"] <= end:
                continue
            if s["tier"] != "A+" and s["mins"] < ab * 60:
                continue
            day = s["time"].date()
            if fills.get(day, 0) >= maxday or (busy is not None and s["time"] < busy):
                continue
            busy = s["exit_time"]
            if s["filled"]:
                fills[day] = fills.get(day, 0) + 1
                taken.append(s)
    return sorted(taken, key=lambda s: s["time"])


def score(trades: list[dict], half: bool) -> dict:
    r = np.array([(s["r_half"] if half else s["r"]) - s["cost"] for s in trades]) if trades else np.zeros(0)
    t = np.array([s["time"] >= SPLIT for s in trades], bool)
    eq = np.concatenate([[0], np.cumsum(r)])
    mon = pd.Series(r, index=[f"{s['time']:%Y-%m}" for s in trades]).groupby(level=0).sum() if trades else pd.Series(dtype=float)
    return dict(n=len(r), win=float((r > 0).mean()) if len(r) else 0, R=float(r.sum()),
                n1=int((~t).sum()), R1=float(r[~t].sum()), n2=int(t.sum()), R2=float(r[t].sum()),
                dd=float((eq - np.maximum.accumulate(eq)).min()), pos_months=int((mon > 0).sum()), months=len(mon),
                oct=float(mon.get("2026-10", 0.0)), monthly={k: round(v, 2) for k, v in mon.items()},
                curve=[[s["time"].strftime("%Y-%m-%d %H:%M"), round(float(v), 2)] for s, v in zip(trades, eq[1:])])


def main() -> None:
    D = {m: A.load(m, False) for m in MKTS}
    S = {}
    for tg in TARGETS:
        print(f"signals at {tg}R target...", flush=True)
        S[tg] = sorted(signals(D, tg), key=lambda s: s["time"])
    rows = []
    for g, ti, ab, st, en, md, tg, hf in itertools.product(GROUPS, TIERS, AB_AFTER, START, END, MAXDAY, TARGETS, HALF):
        if st >= en or (ab and TIERS[ti] == ("A+",)) or (ab and st >= ab * 60):
            continue                                       # duplicates of another row
        tr = plan(S[tg], GROUPS[g], TIERS[ti], ab, st, en, md)
        sc = score(tr, hf)
        rows.append(dict(groups=g, tiers=ti, ab=ab, start=st, end=en, maxday=md, target=tg, half=hf, **sc))
    df = pd.DataFrame(rows).drop_duplicates(["groups", "tiers", "maxday", "target", "half", "n", "R"])   # same trades
    df["r1"], df["r2"] = df.R1 / df.n1.clip(lower=1), df.R2 / df.n2.clip(lower=1)
    print(f"{len(df)} variants")
    ours = df[(df.groups == OURS["groups"]) & (df.tiers == OURS["tiers"]) & (df.ab == OURS["ab"]) & (df.start == OURS["start"])
              & (df.end == OURS["end"]) & (df.maxday == 1) & (df.target == 2.0) & df.half].iloc[0]
    pool = df[(df.n1 >= 40) & (df.n2 >= 40)]
    picked = pool.sort_values("r1", ascending=False).head(10)                  # chosen on the first half only
    both = pool[(pool.R1 > 0) & (pool.R2 > 0)].sort_values("R", ascending=False)
    share = len(both) / max(len(pool), 1)
    print(f"\nours: {ours.n} trades, {ours.R:+.1f}R (1st half {ours.R1:+.1f}, 2nd {ours.R2:+.1f}), Oct {ours.oct:+.1f}")
    print(f"variants with 40+ trades in each half: {len(pool)}; positive in BOTH halves: {len(both)} ({share:.0%})")
    cols = ["groups", "tiers", "ab", "start", "end", "maxday", "target", "half", "n", "win", "R1", "R2", "R", "oct", "pos_months", "dd"]
    print("\nTop 10 picked on the first half -> how they did on the second half:")
    print(picked[cols].round(2).to_string(index=False))
    print("\nTop 10 positive in both halves (full-year hindsight):")
    print(both.head(10)[cols].round(2).to_string(index=False))
    print(f"\nall variants: median full-year R {df.R.median():+.1f}; share of variants > 0: {(df.R > 0).mean():.0%}")

    def hm(m):
        return f"{m // 60}:{m % 60:02d}"

    def label(r):
        return (f"{r.groups} · {r.tiers}{f' (A/B from {r.ab}:00)' if r.ab and r.tiers != 'A+ only' else ''} · {hm(r.start)}-{hm(r.end)}"
                f" · {r.maxday}/day · {r.target:g}R{' · half at 1R' if r.half else ''}")
    best_hold = picked.iloc[0]
    best_both = both.iloc[0] if len(both) else None
    pack = lambda r, tag: dict(tag=tag, label=label(r), **{k: (float(r[k]) if isinstance(r[k], (np.floating, float)) else
                                                         int(r[k]) if isinstance(r[k], (np.integer, int, bool, np.bool_)) else r[k])
                                                        for k in ["n", "win", "R", "R1", "R2", "n1", "n2", "dd", "pos_months", "months", "oct", "monthly", "curve"]})
    data = dict(ours=pack(ours, "Our plan"), held=pack(best_hold, "Best on Nov–Apr, then tested May–Nov"),
                both=pack(best_both, "Best positive in both halves (hindsight)") if best_both is not None else None,
                picked=[pack(r, "") for _, r in picked.iterrows()], top_both=[pack(r, "") for _, r in both.head(10).iterrows()],
                families={k: [dict(name=str(n), variants=len(x), r1=float(x.r1.median()), r2=float(x.r2.median()),
                                   both=float(((x.R1 > 0) & (x.R2 > 0)).mean())) for n, x in df.groupby(k)]
                          for k in ["tiers", "groups", "target", "half", "maxday", "end", "ab"]},
                n_variants=len(df), n_pool=len(pool), n_both=len(both), share_pos=float((df.R > 0).mean()),
                median_R=float(df.R.median()))
    for k in ("picked", "top_both"):
        for x in data[k]:
            x.pop("curve")
    tpl = (ROOT / "tools" / "plan_search_template.html").read_text()
    out = ROOT / "reports" / "plan_search.html"
    out.write_text(tpl.replace("__DATA__", json.dumps(data)))
    print(f"\n{out}")
    if sys.platform == "darwin" and "--no-open" not in sys.argv:
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
