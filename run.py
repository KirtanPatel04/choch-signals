"""MES backtester.

Examples:
  python run.py                          # Craig-style CHoCH-at-level scalp, last ~29 days
  python run.py --sweep                  # test many variations and rank them
  python run.py --strategy orb --r 2
  python run.py --csv my_export.txt      # use a NinjaTrader / TradingView export instead
"""
from __future__ import annotations

import argparse
import itertools
import webbrowser
from dataclasses import replace
from pathlib import Path

import pandas as pd

from backtester.data import load_data
from backtester.engine import Settings, run_backtest
from backtester.report import build_report
from backtester.strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent


def run_one(df, name, s):
    orders, lines = STRATEGIES[name](df, s)
    return run_backtest(df, orders, s), lines


def fmt(st: dict) -> str:
    return (f"{st['trades']:>4} trades | win {st['win_rate']:5.1f}% | avg {st['avg_r']:+.2f}R | "
            f"net ${st['net_pnl']:>9,.2f} | ${st['avg_per_day']:>7,.2f}/day | "
            f"max DD ${st['max_drawdown']:>8,.2f} | worst streak {st['worst_losing_streak']}")


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest MES strategies on 1-minute bars.")
    p.add_argument("--strategy", default="choch_scalp", choices=list(STRATEGIES))
    p.add_argument("--csv", help="NinjaTrader or TradingView 1-minute export")
    p.add_argument("--csv-tz", default="America/Chicago", help="time zone of CSV timestamps")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--refresh", action="store_true", help="re-download even if cached today")
    p.add_argument("--r", type=float, help="reward-to-risk target, e.g. 2, 3, 4")
    p.add_argument("--risk", type=float, help="$ risk per trade (default 100)")
    p.add_argument("--balance", type=float, help="starting balance (default 500)")
    p.add_argument("--max-trades", type=int, help="max trades per day (default 5)")
    p.add_argument("--max-contracts", type=int, help="largest position in contracts (default 10)")
    p.add_argument("--max-daily-loss", type=float, help="$ loss that ends the day; trades are sized to fit what's left (default off)")
    p.add_argument("--max-losses", type=int, help="stop trading after this many losses a day (default 3)")
    p.add_argument("--min-trades", type=int, help="choch_scalp: use backup setups until the day has this many trades (default 3, 0 = A+ only)")
    p.add_argument("--exit", choices=["runner", "fixed"],
                   help="runner: partial at 2R, breakeven past the recent high/low, trail to 8R (default); "
                        "fixed: plain R target")
    p.add_argument("--window", help="entry window in CT, e.g. 08:30-10:30")
    p.add_argument("--bias", action="store_true", help="only trade the 1D candle direction")
    p.add_argument("--partial-r", type=float, help="take partial profit at this R (default 2, 0 = off)")
    p.add_argument("--partial-frac", type=float, help="share of contracts taken off there (default 0.25)")
    p.add_argument("--trend", choices=["structure", "none", "daily", "vwap", "ema", "1h"],
                   help="choch_scalp: longs only in an uptrend, shorts only in a downtrend, "
                        "judged by this (default structure = 15M market structure)")
    p.add_argument("--min-score", type=float, help="choch_scalp: only take setups scoring this or more (default 6)")
    p.add_argument("--no-breakeven", action="store_true")
    p.add_argument("--sweep", action="store_true", help="test many variations and rank them")
    p.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    a = p.parse_args()

    s = Settings()
    if a.r is not None: s.r_multiple = a.r
    if a.risk is not None: s.risk_per_trade = a.risk
    if a.balance is not None: s.start_balance = a.balance
    if a.max_trades is not None: s.max_trades_per_day = a.max_trades
    if a.max_contracts is not None: s.max_contracts = a.max_contracts
    if a.max_daily_loss is not None: s.max_daily_loss = a.max_daily_loss
    if a.max_losses is not None: s.max_losses_per_day = a.max_losses
    if a.min_trades is not None: s.min_trades_per_day = a.min_trades
    if a.exit: s.exit_mode = a.exit
    if a.window: s.entry_start, s.entry_end = a.window.split("-")
    if a.bias: s.use_daily_bias = True
    if a.partial_r is not None: s.partial_r = a.partial_r
    if a.partial_frac is not None: s.partial_frac = a.partial_frac
    if a.trend: s.trend = a.trend
    if a.min_score is not None: s.min_score = a.min_score
    if a.no_breakeven: s.breakeven_at_r = 0.0

    df = load_data(a.csv, a.days, a.refresh, a.csv_tz)
    print(f"Loaded {len(df):,} bars: {df.index[0]:%Y-%m-%d %H:%M} -> {df.index[-1]:%Y-%m-%d %H:%M} CT\n")

    sweep_rows = None
    name = a.strategy
    if a.sweep:
        sweep_rows = []
        combos = itertools.product(["fvg_scalp", "orb"], [2.0, 3.0, 4.0], [True, False], [1.0, 0.0])
        for strat, r, bias, be in combos:
            if strat == "orb" and not bias:
                continue  # ORB doesn't use the 1D bias
            v = replace(s, r_multiple=r, use_daily_bias=bias, breakeven_at_r=be)
            res, _ = run_one(df, strat, v)
            row = dict(strategy=strat, r_multiple=r, use_daily_bias=bias, breakeven_at_r=be,
                       **res["stats"])
            sweep_rows.append(row)
            print(f"{strat:<10} {r:.0f}R bias={'on ' if bias else 'off'} BE={'on ' if be else 'off'} | {fmt(res['stats'])}")
        sweep_rows.sort(key=lambda r: r["net_pnl"], reverse=True)
        best = sweep_rows[0]
        name = best["strategy"]
        s = replace(s, r_multiple=best["r_multiple"], use_daily_bias=best["use_daily_bias"],
                    breakeven_at_r=best["breakeven_at_r"])
        out_dir = ROOT / "reports"
        out_dir.mkdir(exist_ok=True)
        pd.DataFrame(sweep_rows).to_csv(out_dir / "sweep.csv", index=False)
        print(f"\nBest variation: {name}, {s.r_multiple:.0f}R, bias {'on' if s.use_daily_bias else 'off'}, "
              f"breakeven {'on' if s.breakeven_at_r else 'off'}")

    res, lines = run_one(df, name, s)
    st = res["stats"]
    print(f"\n{name}: {fmt(st)}")
    verdict = "PROFITABLE" if st["net_pnl"] > 0 else "NOT PROFITABLE"
    print(f"=> {verdict} over {st['days']} trading days, ending balance ${st['end_balance']:,.2f}")
    if st["blown"]:
        print(f"=> Account fell too low to keep trading on {st['blown_at'][:10]}")

    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    if res["trades"]:
        pd.DataFrame(res["trades"]).to_csv(out_dir / f"trades_{name}.csv", index=False)
    report = build_report(df, res, lines, s, name, out_dir / f"report_{name}.html", sweep_rows)
    print(f"\nReport: {report}")
    if not a.no_open:
        webbrowser.open(report.as_uri())


if __name__ == "__main__":
    main()
