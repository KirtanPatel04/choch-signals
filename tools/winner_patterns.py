"""Simulate every candidate setup on its own, tag it with traits, and compare winners vs losers.
Run from the project folder: python tools/winner_patterns.py"""
import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
from dataclasses import replace
import numpy as np, pandas as pd
import backtester.strategies as S
from backtester.data import load_data
from backtester.engine import Settings, run_backtest
df = load_data(None, 29, False, "America/Chicago")
base = replace(Settings(), risk_per_trade=200, start_balance=1e6, max_losses_per_day=99, max_trades_per_day=99,
               min_trades_per_day=0, trend="none", min_score=0)
cands, _ = S._choch_setups(df, base)
mins = df.index.hour * 60 + df.index.minute
cands = {i: o for i, o in cands.items() if 510 <= mins[i] < 900}
tr = {k: S.trend(df, replace(base, trend=k)) for k in ["structure", "vwap", "1h", "ema", "daily"]}
# VWAP level
tp = (df.high + df.low + df.close) / 3; sd = S.session_date(df.index)
vwap = ((tp * df.volume).groupby(sd).cumsum() / df.volume.groupby(sd).cumsum()).to_numpy()
# news proxy: 7:30 CT bar range vs median 1M range of that morning's overnight
rng = (df.high - df.low)
day = np.asarray(df.index.date)
news = {}
for d in np.unique(day):
    sel = (day == d)
    at730 = sel & (mins == 450)
    pre = sel & (mins >= 360) & (mins < 450)
    if at730.any() and pre.any():
        news[d] = float(rng[at730].iloc[0] / max(rng[pre].median(), 0.25))
# first-hour range per day vs average (trend day proxy)
rows = []
print(f"{len(cands)} candidate setups in 8:30-15:00 over the month; simulating each on its own...")
for i, o in cands.items():
    r = run_backtest(df, {i: o}, base)
    if not r["trades"]:
        continue
    t = r["trades"][0]; w = o["why"]; side = o["side"]
    stop_pts = t["stop_pts"]
    rows.append(dict(
        time=df.index[i], side="Long" if side == 1 else "Short", r=t["r"], pnl=t["pnl"], reason=t["reason"],
        score=o["score"], strength=w["strength"], swept=w["swept"], disp=w["disp"],
        prev_day="prev day" in w["kinds"], m15="15M" in w["kinds"], only_5m=w["kinds"] in ("5M high", "5M low", "5M high+5M low"),
        n_kinds=w["kinds"].count("+") + 1,
        **{f"with_{k}": tr[k][i] * side > 0 for k in tr},
        vwap_dist_r=(df.close.iloc[i] - vwap[i]) * side / stop_pts,
        stop_pts=stop_pts, hour=mins[i] // 60 + (mins[i] % 60) / 60,
        dow=df.index[i].day_name()[:3], news_ratio=news.get(day[i], np.nan),
    ))
T = pd.DataFrame(rows)
T["win"] = T.r > 0
T.to_csv("reports/setup_traits.csv", index=False)
print(f"{len(T)} filled; overall win {T.win.mean():.0%}, avg R {T.r.mean():+.2f}\n")
def show(name, col):
    g = T.groupby(col).agg(n=("r", "size"), win=("win", "mean"), avgR=("r", "mean"), totR=("r", "sum"))
    g["win"] = (g.win * 100).round(0); print(f"--- {name}"); print(g.round(2).to_string(), "\n")
for k in tr: show(f"trading WITH {k} trend", f"with_{k}")
show("side", "side")
show("level includes previous day H/L", "prev_day")
show("level includes 15M swing", "m15")
show("only 5M levels", "only_5m")
show("number of level types stacked", "n_kinds")
show("swept the level (poked through, closed back)", "swept")
T["disp_b"] = pd.cut(T.disp, [0, 1, 1.5, 2, 3.01]); show("displacement candle (x avg range)", "disp_b")
T["score_b"] = pd.cut(T.score, [0, 5, 6, 7, 8, 20]); show("score", "score_b")
T["stop_b"] = pd.cut(T.stop_pts, [0, 2.01, 3, 4, 6, 50]); show("stop size (pts)", "stop_b")
T["tod"] = pd.cut(T.hour, [8.5, 9.5, 10.5, 12, 13.5, 15], right=False, labels=["8:30-9:30", "9:30-10:30", "10:30-12", "12-1:30", "1:30-3"]); show("time of day (CT)", "tod")
T["vw_b"] = pd.cut(T.vwap_dist_r, [-99, -2, 0, 2, 5, 99]); show("distance from VWAP in trade direction (R)", "vw_b")
T["news_b"] = pd.cut(T.news_ratio, [0, 2, 4, 99], labels=["quiet 7:30", "some 7:30 move", "big 7:30 move (news-like)"]); show("7:30 CT data release reaction", "news_b")
show("day of week", "dow")
show("exit reason", "reason")
