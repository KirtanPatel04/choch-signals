"""Bar-by-bar trade simulator with realistic fills."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Settings:
    # --- risk ---------------------------------------------------------------
    risk_per_trade: float = 100.0     # $ lost if the stop hits (before fees/slippage)
    r_multiple: float = 8.0           # target = R x stop distance (runner mode: the cap)
    stop_points: float = 20.0         # used by strategies that don't set their own stop
    min_stop_points: float = 2.0      # stops tighter than this get widened to it
    max_contracts: int = 10
    margin_per_contract: float = 50.0  # NinjaTrader MES day margin
    breakeven_at_r: float = 1.0       # move stop to entry after a close this far in profit (0 = off)
    # --- runner exits (exit_mode="runner") --------------------------------------
    # Stop goes to entry once a 1M candle closes past the recent high (longs) / low
    # (shorts). Once price reaches lock_at_r, the stop moves to lock_r in profit and then
    # trails behind each new 1M swing; r_multiple caps the target. Without a recent
    # high/low beyond entry, breakeven_at_r is the breakeven trigger instead.
    exit_mode: str = "runner"         # "runner" or "fixed" (plain R target + breakeven)
    resistance_bars: int = 60         # minutes before entry that define the recent high/low
    lock_at_r: float = 2.0
    lock_r: float = 1.0
    # Take partial profit: when price reaches partial_r, close partial_frac of the
    # contracts there and move the stop to at least entry; the rest keeps running toward
    # r_multiple under the rules above. 0 = off. Needs 2+ contracts.
    partial_r: float = 2.0
    partial_frac: float = 0.25
    trail_tf: str = "1min"            # trail behind swings on this timeframe: 1min, 3min, 5min, 15min
    # --- when you trade -------------------------------------------------------
    entry_start: str = "08:30"        # CT (9:30 ET New York open)
    entry_end: str = "15:00"          # CT
    flatten_time: str = "15:40"       # CT; intraday margin ends 3:45
    max_trades_per_day: int = 5
    max_losses_per_day: int = 3
    max_daily_loss: float = 0.0       # $; stop for the day once losses reach this, and size each trade to fit what's left (0 = off)
    min_trades_per_day: int = 3       # orders marked tier 2 are only taken while the day has fewer trades than this
    backup_trend: str = "vwap"        # choch_scalp: trend filter for tier-2 backup setups ("" = none)
    limit_expiry_bars: int = 10       # minutes a limit entry stays working
    # --- costs ----------------------------------------------------------------
    fees_per_contract_rt: float = 1.50
    slippage_ticks: int = 1           # on market entries and stop exits
    point_value: float = 5.0          # MES = $5 per point
    tick_size: float = 0.25
    # --- account --------------------------------------------------------------
    start_balance: float = 500.0
    # --- strategy knobs -------------------------------------------------------
    use_daily_bias: bool = False      # only trade in the direction of the previous daily candle
    trend: str = "structure"          # choch_scalp trend filter: none, daily, vwap, ema, structure, 1h
    min_gap_points: float = 1.0       # smallest 15M gap that counts
    zone_lookback_days: int = 3       # 15M gaps from the last few days
    touch_window_bars: int = 45       # minutes after touching a zone to find the 1M entry
    orb_minutes: int = 15
    level_tfs: tuple = ("15min", "5min")  # choch_scalp: swing levels from these timeframes
    level_near_points: float = 1.0    # choch_scalp: "at the level" = within this many points
    sweep_bars: int = 20              # choch_scalp: minutes back to look for the push into a level
    min_score: float = 6.0            # choch_scalp: only trade setups scoring at least this
    min_room_r: float = 0.0           # choch_scalp: need this many R of room before the next level (0 = off)
    room_min_weight: int = 2          # choch_scalp: levels that count as "in the way" (2 = 15M + prev day)
    stop_at: str = "swing"            # choch_scalp: "swing" (past the push into the level) or "candle"

    def to_dict(self) -> dict:
        return asdict(self)


def _hm(s: str) -> int:
    h, m = map(int, s.split(":"))
    return h * 60 + m


def _swings(df: pd.DataFrame, tf: str) -> tuple[np.ndarray, np.ndarray]:
    """Swing highs / lows on timeframe tf, placed on the 1M bar at whose close the swing
    is confirmed (the candle after the swing candle has closed); NaN elsewhere."""
    n = len(df)
    if tf in ("1min", "1m"):
        bars, pos = df, np.arange(n)
    else:
        bars = df.resample(tf, label="left", closed="left").agg(
            {"high": "max", "low": "min"}).dropna()
        # last 1M bar inside each candle = where that candle's close is known
        ends = bars.index + pd.Timedelta(tf) - pd.Timedelta("1min")
        pos = np.minimum(df.index.searchsorted(ends, side="right") - 1, n - 1)
    h, l = bars["high"].to_numpy(), bars["low"].to_numpy()
    sw_hi, sw_lo = np.full(n, np.nan), np.full(n, np.nan)
    if len(bars) > 2:
        j = np.arange(1, len(bars) - 1)
        is_hi = (h[j] > h[j - 1]) & (h[j] >= h[j + 1])
        is_lo = (l[j] < l[j - 1]) & (l[j] <= l[j + 1])
        sw_hi[pos[j + 1][is_hi]] = h[j][is_hi]
        sw_lo[pos[j + 1][is_lo]] = l[j][is_lo]
    return sw_hi, sw_lo


def run_backtest(df: pd.DataFrame, orders: dict, s: Settings) -> dict:
    """Rules:
    - Orders are placed at the close of the bar that created them and can fill from the
      next bar on. Market orders fill at the next open plus slippage.
    - Limit orders fill when price trades through the limit, and are cancelled if not
      filled within `expire` bars or if price runs to the stop first.
    - If one bar touches both the stop and the target, the stop counts first.
    - Size = risk_per_trade / (stop distance x $5), rounded down, capped by max_contracts
      and by margin. If even 1 contract would risk more than risk_per_trade, the trade
      is skipped.
    """
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    times = df.index
    mins = np.asarray(times.hour * 60 + times.minute)
    dates = np.asarray(times.date)
    es, ee, fl = _hm(s.entry_start), _hm(s.entry_end), _hm(s.flatten_time)
    slip = s.slippage_ticks * s.tick_size
    sw_hi, sw_lo = _swings(df, s.trail_tf)

    trades: list[dict] = []
    skipped = {"stop too wide": 0, "limit not filled": 0, "daily limit": 0}
    bal = s.start_balance
    pos = None
    working = None          # order waiting to fill
    day = None
    day_trades = day_losses = 0
    day_pnl = 0.0
    blown_at = None

    def size_for(stop_pts: float) -> int:
        per = stop_pts * s.point_value
        risk = s.risk_per_trade
        if s.max_daily_loss > 0:
            risk = min(risk, s.max_daily_loss + day_pnl)
        n = int(risk // per) if per > 0 else 0
        return max(0, min(n, s.max_contracts, int(bal // s.margin_per_contract)))

    def open_pos(i: int, side: int, entry: float, stop_px, note: str) -> bool:
        nonlocal pos, day_trades
        stop_pts = abs(entry - stop_px) if stop_px is not None else s.stop_points
        stop_pts = max(stop_pts, s.min_stop_points)
        qty = size_for(stop_pts)
        if qty < 1:
            skipped["stop too wide"] += 1
            return False
        prior = slice(max(0, i - s.resistance_bars), i)
        level = (h[prior].max() if side == 1 else l[prior].min()) if i > 0 else np.nan
        if not (level - entry) * side > 0:
            level = np.nan  # nothing in the way: fall back to breakeven_at_r
        pos = dict(side=side, entry=entry, time=times[i], date=dates[i], qty=qty,
                   stop=entry - side * stop_pts, stop_pts=stop_pts,
                   target=entry + side * stop_pts * s.r_multiple, be=False, note=note,
                   level=level, open_qty=qty, banked=0.0, banked_pts=0.0, took_partial=False)
        day_trades += 1
        return True

    def close(i: int, price: float, reason: str) -> None:
        nonlocal pos, bal, day_losses, day_pnl, blown_at
        p = pos
        last_pts = (price - p["entry"]) * p["side"]
        pts = (p["banked_pts"] + last_pts * p["open_qty"]) / p["qty"]   # average per contract
        fees = s.fees_per_contract_rt * p["qty"]
        pnl = p["banked"] + last_pts * s.point_value * p["open_qty"] - fees
        if p["took_partial"]:
            reason = "partial+" + reason
        risk = p["stop_pts"] * s.point_value * p["qty"]
        bal += pnl
        day_pnl += pnl
        if pnl < 0:
            day_losses += 1
        trades.append(dict(
            entry_time=p["time"], exit_time=times[i],
            side="Long" if p["side"] == 1 else "Short", contracts=p["qty"],
            entry=p["entry"], exit=price, stop_pts=p["stop_pts"], points=pts,
            stop=p["entry"] - p["side"] * p["stop_pts"], target=p["target"],
            partial=(p["entry"] + p["side"] * p["stop_pts"] * s.partial_r
                     if p["took_partial"] else None),
            pnl=pnl, risk=risk, r=pnl / risk, reason=reason, setup=p["note"], balance=bal))
        pos = None
        if blown_at is None and bal < min(s.risk_per_trade, s.margin_per_contract * 2):
            blown_at = times[i]

    for i in range(len(df)):
        if dates[i] != day:
            day, day_trades, day_losses, day_pnl = dates[i], 0, 0, 0.0
            if working is not None:
                skipped["limit not filled"] += 1
                working = None

        # 1. flatten before the intraday margin cutoff
        if pos is not None and (mins[i] >= fl or pos["date"] != dates[i]):
            close(i, o[i] - pos["side"] * slip, "flatten")

        # 2. fill working orders
        just_filled = False
        if working is not None and pos is None and mins[i] < fl:
            w = working
            side = w["side"]
            if w["limit"] is None:
                just_filled = open_pos(i, side, o[i] + side * slip, w["stop"], w["note"])
                working = None
            elif i > w["expire_at"]:
                skipped["limit not filled"] += 1
                working = None
            else:
                stop_px, lim = w["stop"], w["limit"]
                ran_to_stop = (o[i] <= stop_px) if side == 1 else (o[i] >= stop_px)
                touched = (l[i] <= lim) if side == 1 else (h[i] >= lim)
                if ran_to_stop:
                    skipped["limit not filled"] += 1
                    working = None
                elif touched:
                    better = (o[i] < lim) if side == 1 else (o[i] > lim)
                    just_filled = open_pos(i, side, o[i] if better else lim, stop_px, w["note"])
                    working = None

        # 3. manage the open position
        if pos is not None:
            side, stop, tgt = pos["side"], pos["stop"], pos["target"]
            hit_stop = l[i] <= stop if side == 1 else h[i] >= stop
            hit_tgt = h[i] >= tgt if side == 1 else l[i] <= tgt
            if hit_stop:
                gapped = (o[i] < stop) if side == 1 else (o[i] > stop)
                locked = (stop - pos["entry"]) * side
                close(i, (o[i] if gapped and not just_filled else stop) - side * slip,
                      "trail" if locked > 0 else "breakeven" if pos["be"] else "stop")
            elif hit_tgt:
                gapped = (o[i] > tgt) if side == 1 else (o[i] < tgt)
                close(i, o[i] if gapped and not just_filled else tgt, "target")
            elif s.exit_mode == "runner":
                entry, one_r = pos["entry"], pos["stop_pts"]
                best = h[i] if side == 1 else l[i]
                if s.partial_r > 0 and not pos["took_partial"] and pos["qty"] >= 2 \
                        and (best - entry) * side >= one_r * s.partial_r:
                    n = min(max(1, int(pos["qty"] * s.partial_frac)), pos["qty"] - 1)
                    gain = one_r * s.partial_r
                    pos["banked"] += gain * s.point_value * n
                    pos["banked_pts"] += gain * n
                    pos["open_qty"] -= n
                    pos["took_partial"] = True
                    if (entry - pos["stop"]) * side > 0:
                        pos["stop"], pos["be"] = entry, True
                if not pos["be"]:
                    trigger = pos["level"]
                    if np.isnan(trigger) and s.breakeven_at_r > 0:
                        trigger = entry + side * one_r * s.breakeven_at_r
                    if not np.isnan(trigger) and (c[i] - trigger) * side > 0:
                        pos["stop"], pos["be"] = entry, True
                if pos.get("run"):
                    swing = sw_lo[i] if side == 1 else sw_hi[i]
                    if not np.isnan(swing):
                        trail = swing - side * s.tick_size
                        if (trail - pos["stop"]) * side > 0 and (c[i] - trail) * side > 0:
                            pos["stop"] = trail
                if s.lock_at_r > 0 and (best - entry) * side >= one_r * s.lock_at_r:
                    floor = entry + side * one_r * s.lock_r
                    if (floor - pos["stop"]) * side > 0:
                        pos["stop"], pos["be"] = floor, True
                    pos["run"] = True
            elif s.breakeven_at_r > 0 and not pos["be"]:
                trigger = pos["entry"] + side * pos["stop_pts"] * s.breakeven_at_r
                if (c[i] - trigger) * side >= 0:
                    pos["stop"], pos["be"] = pos["entry"], True

        # 4. new orders, decided at this bar's close
        order = orders.get(i)
        if order is not None and pos is None and working is None and blown_at is None \
                and es <= mins[i] < ee:
            if day_trades >= s.max_trades_per_day or day_losses >= s.max_losses_per_day \
                    or (s.max_daily_loss > 0 and day_pnl <= -s.max_daily_loss):
                skipped["daily limit"] += 1
            elif order.get("tier", 1) > 1 and day_trades >= s.min_trades_per_day:
                pass  # backup setup, only used to reach min_trades_per_day
            else:
                working = dict(order, expire_at=i + order.get("expire", 1))

    if pos is not None:
        close(len(df) - 1, c[-1], "end of data")

    return dict(trades=trades, skipped=skipped, blown_at=blown_at,
                stats=summarize(trades, s, df, blown_at))


def summarize(trades: list[dict], s: Settings, df: pd.DataFrame, blown_at) -> dict:
    m = df.index.hour * 60 + df.index.minute
    rth = df[(m >= 510) & (m < 900)]
    n_days = len(np.unique(rth.index.date)) or 1
    out = dict(trades=0, win_rate=0.0, net_pnl=0.0, end_balance=s.start_balance,
               return_pct=0.0, avg_win=0.0, avg_loss=0.0, profit_factor=0.0, avg_r=0.0,
               expectancy=0.0, max_drawdown=0.0, max_drawdown_pct=0.0,
               worst_losing_streak=0, days=n_days, trades_per_day=0.0, avg_per_day=0.0,
               best_day=0.0, worst_day=0.0, green_days=0, red_days=0, days_300_plus=0,
               fees_paid=0.0, blown=blown_at is not None,
               blown_at=str(blown_at) if blown_at is not None else None)
    if not trades:
        return out
    t = pd.DataFrame(trades)
    wins, losses = t[t.pnl > 0], t[t.pnl <= 0]
    eq = np.r_[s.start_balance, s.start_balance + t.pnl.cumsum().to_numpy()]
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    streak = worst = 0
    for p in t.pnl:
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)
    daily = t.groupby(t.exit_time.dt.date).pnl.sum()
    gross_loss = -losses.pnl.sum()
    out.update(
        trades=len(t), win_rate=len(wins) / len(t) * 100, net_pnl=t.pnl.sum(),
        end_balance=eq[-1], return_pct=(eq[-1] / s.start_balance - 1) * 100,
        avg_win=wins.pnl.mean() if len(wins) else 0.0,
        avg_loss=losses.pnl.mean() if len(losses) else 0.0,
        profit_factor=(wins.pnl.sum() / gross_loss) if gross_loss > 0 else float("inf"),
        avg_r=t.r.mean(), expectancy=t.pnl.mean(),
        max_drawdown=dd.min(), max_drawdown_pct=(dd / peak).min() * 100,
        worst_losing_streak=worst, trades_per_day=len(t) / n_days,
        avg_per_day=t.pnl.sum() / n_days, best_day=daily.max(), worst_day=daily.min(),
        green_days=int((daily > 0).sum()), red_days=int((daily < 0).sum()),
        days_300_plus=int((daily >= 300).sum()),
        fees_paid=float((t.contracts * s.fees_per_contract_rt).sum()))
    return out
