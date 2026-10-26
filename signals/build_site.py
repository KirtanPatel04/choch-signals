"""Scan MES, MYM, MNQ and SOLUSD for A+ / A / B CHoCH signals and write docs/data/signals.json for the dashboard.

Runs on GitHub Actions on a schedule (see .github/workflows/signals.yml), so nothing has to run locally.
Data: Yahoo Finance 1-minute bars, last ~29 days (may lag a few minutes).

    python -m signals.build_site
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backtester.data import download_yahoo  # noqa: E402
from signals.engine import TZ, find_signals, session_date  # noqa: E402

# name: (Yahoo symbol, tick, $ per point per contract, point scale vs MES or None = measure, unit)
MARKETS = {
    "MES": ("MES=F", 0.25, 5.0, 1.0, "contracts"),
    "MYM": ("MYM=F", 1.0, 0.5, 6.0, "contracts"),
    "MNQ": ("MNQ=F", 0.25, 2.0, 5.5, "contracts"),
    "SOLUSD": ("SOL-USD", 0.01, 1.0, None, "SOL"),
}
SESSIONS = ("NY", "Asia", "London")
OUT = ROOT / "docs" / "data" / "signals.json"


def typical_range(d: pd.DataFrame) -> float:
    r = (d.high - d.low)[d.index.hour.isin(range(9, 15))]
    return float(r[r > 0].median())


def confidence(net_r: float, n: int) -> int:
    """0-100 from the last ~30 days of the same market / session / grade. 50 = broke even.
    Net R is shrunk toward 0 for small samples, so a few lucky trades can't score high."""
    return int(max(1, min(99, round(50 + 60 * net_r / (n + 10)))))


def clean(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.tz_convert(TZ).isoformat()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def main() -> None:
    raw, errors = {}, []
    for name, (sym, *_rest) in MARKETS.items():
        try:
            raw[name] = download_yahoo(sym, 29)
        except Exception as e:  # keep going with the markets that downloaded
            errors.append(f"{name}: {e}")
    if "MES" not in raw:
        raise SystemExit("MES data unavailable: " + "; ".join(errors))
    ref = typical_range(raw["MES"])

    all_sigs, data_through = [], {}
    for name, d in raw.items():
        sym, tick, pv, scale, unit = MARKETS[name]
        scale = scale or typical_range(d) / ref
        data_through[name] = clean(d.index.max() + pd.Timedelta(minutes=1))
        for s in find_signals(d, tick, scale, sessions=SESSIONS):
            s["market"] = name
            all_sigs.append(s)
    S = pd.DataFrame(all_sigs)

    stats = []
    for (m, sess, tier), g in S.groupby(["market", "session", "tier"]):
        f = g[g.r.notna() & g.status.isin(["target", "stop", "closed"])]
        n, net = len(f), float(f.r.sum()) if len(f) else 0.0
        stats.append(dict(market=m, session=sess, tier=tier, signals=len(g), trades=n,
                          wins=int((f.r > 0.05).sum()), losses=int((f.r < -0.05).sum()),
                          win_rate=round(float((f.r > 0).mean()), 3) if n else None,
                          avg_r=round(net / n, 3) if n else None, net_r=round(net, 2),
                          confidence=confidence(net, n)))
    conf = {(x["market"], x["session"], x["tier"]): x["confidence"] for x in stats}

    # signals from the last 3 session days, newest first
    S["day"] = session_date(pd.DatetimeIndex(S.time))
    keep_days = sorted(S.day.unique())[-3:]
    recent = S[S.day.isin(keep_days)].sort_values("time", ascending=False)
    rows = []
    for s in recent.to_dict("records"):
        _, tick, pv, _, unit = MARKETS[s["market"]]
        rows.append({k: clean(v) for k, v in dict(
            market=s["market"], session=s["session"], tier=s["tier"], side="BUY" if s["side"] == 1 else "SELL",
            time=s["time"], entry=round(s["entry"], 4), stop=round(s["stop"], 4), target=round(s["target"], 4),
            risk_pts=round(s["risk_pts"], 4), risk_ticks=int(round(s["risk_pts"] / tick)), strength=round(s["strength"], 2),
            status=s["status"], r=None if s["r"] is None or pd.isna(s["r"]) else round(float(s["r"]), 2),
            fill_time=s.get("fill_time"), exit_time=s.get("exit_time"),
            confidence=conf.get((s["market"], s["session"], s["tier"])), day=str(s["day"])).items()})

    out = dict(
        generated=datetime.now(timezone.utc).isoformat(),
        data_through=data_through,
        errors=errors,
        markets={n: dict(tick=t, point_value=pv, unit=u) for n, (_, t, pv, _, u) in MARKETS.items()},
        sessions={"NY": "8:30-3:00 CT", "Asia": "7:00-11:00 PM CT", "London": "2:00-5:00 AM CT"},
        period=dict(start=clean(min(d.index.min() for d in raw.values())), end=clean(max(d.index.max() for d in raw.values()))),
        stats=sorted(stats, key=lambda x: -x["confidence"]),
        signals=rows,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {OUT}: {len(rows)} recent signals, {len(stats)} stat groups, errors: {errors or 'none'}")


if __name__ == "__main__":
    main()
