"""Entry rules.

Each strategy returns (orders, lines):
  orders - dict {bar_index: order}. An order is decided at that bar's CLOSE, so the engine
           can only fill it on later bars (no peeking). Order fields:
             side   +1 long / -1 short
             limit  price for a limit entry, or None for a market entry at the next open
             stop   stop-loss price, or None to use Settings.stop_points from the fill
             expire bars a limit order stays working before it's cancelled
             note   short label shown in the trade list
  lines  - dict of name -> array of prices to draw on the chart (NaN = don't draw).

Targets, position size, breakeven, daily limits and the flatten time live in engine.py.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd


def session_date(idx: pd.DatetimeIndex) -> np.ndarray:
    """CME trade date: the session opening at 5:00 PM CT belongs to the next day."""
    return np.asarray((idx + pd.Timedelta(hours=7)).date)


def minutes(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(idx.hour * 60 + idx.minute)


def snap(price: float, tick: float, side: int) -> float:
    """Round a limit price onto the tick grid, toward the less generous fill."""
    q = price / tick
    return (np.floor(q) if side == 1 else np.ceil(q)) * tick


def daily_bias(df: pd.DataFrame) -> np.ndarray:
    """+1 if the previous session's daily candle was green, -1 if red, 0 if unknown."""
    sd = session_date(df.index)
    g = df.groupby(sd)
    direction = np.sign(g["close"].last() - g["open"].first())
    return pd.Series(sd).map(direction.shift(1)).fillna(0).astype(int).to_numpy()


def _htf(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Higher-timeframe candles, indexed so each 1M bar only sees candles that have
    already closed by the end of that 1M bar."""
    bars = df.resample(tf, label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    bars.index = bars.index - pd.Timedelta("1min")
    return bars


def _onto(series: pd.Series, df: pd.DataFrame) -> np.ndarray:
    return series.reindex(df.index, method="ffill").fillna(0).astype(int).to_numpy()


def trend(df: pd.DataFrame, s) -> np.ndarray:
    """+1 bullish, -1 bearish, 0 unknown/flat, per 1M bar, from s.trend:
      none       no filter (longs and shorts both allowed)
      daily      previous session's daily candle green / red
      vwap       last close above / below the session VWAP
      ema        15M EMA 20 above / below 15M EMA 50
      structure  15M market structure: last 15M close broke above the latest 15M swing
                 high (bullish) or below the latest 15M swing low (bearish)
      1h         1H close above / below the 1H EMA 20"""
    mode = "daily" if s.use_daily_bias and s.trend == "none" else s.trend
    if mode == "none":
        return np.zeros(len(df), int)
    if mode == "daily":
        return daily_bias(df)
    if mode == "vwap":
        tp = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"].where(df["volume"] > 0, 1.0)
        sd = session_date(df.index)
        vwap = (tp * vol).groupby(sd).cumsum() / vol.groupby(sd).cumsum()
        return np.sign(df["close"] - vwap).astype(int).to_numpy()
    if mode == "ema":
        b = _htf(df, "15min")
        d = b["close"].ewm(span=20, adjust=False).mean() - b["close"].ewm(span=50, adjust=False).mean()
        return _onto(np.sign(d), df)
    if mode == "1h":
        b = _htf(df, "60min")
        return _onto(np.sign(b["close"] - b["close"].ewm(span=20, adjust=False).mean()), df)
    if mode == "structure":
        b = _htf(df, "15min")
        hi, lo, cl = (b[k].to_numpy() for k in ("high", "low", "close"))
        out, state, sh, sl = np.zeros(len(b), int), 0, None, None
        for k in range(1, len(b)):
            if k >= 2 and hi[k - 1] > hi[k - 2] and hi[k - 1] >= hi[k]:
                sh = hi[k - 1]
            if k >= 2 and lo[k - 1] < lo[k - 2] and lo[k - 1] <= lo[k]:
                sl = lo[k - 1]
            if sh is not None and cl[k] > sh:
                state = 1
            elif sl is not None and cl[k] < sl:
                state = -1
            out[k] = state
        return _onto(pd.Series(out, index=b.index), df)
    raise ValueError(f"unknown trend mode {mode!r}")


def fvg_zones(df: pd.DataFrame, min_gap: float, tf: str = "15min") -> list[dict]:
    """Fair-value gaps: 3 candles where candle 1's wick and candle 3's wick don't overlap.
    Bullish when candle 3's low is above candle 1's high; bearish is the mirror.
    A zone only becomes usable after candle 3 has CLOSED."""
    bars = df.resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    step = pd.Timedelta(tf)
    t = bars.index
    hi, lo, op, cl = (bars[k].to_numpy() for k in ("high", "low", "open", "close"))
    zones = []
    for i in range(2, len(bars)):
        if t[i] - t[i - 1] != step or t[i - 1] - t[i - 2] != step:
            continue  # no gaps across the daily halt or the weekend
        when = t[i] + step
        if lo[i] - hi[i - 2] >= min_gap and cl[i - 1] > op[i - 1]:
            zones.append(dict(side=1, bottom=hi[i - 2], top=lo[i], active_from=when))
        if lo[i - 2] - hi[i] >= min_gap and cl[i - 1] < op[i - 1]:
            zones.append(dict(side=-1, bottom=hi[i], top=lo[i - 2], active_from=when))
    for z in zones:
        z["mid"] = (z["top"] + z["bottom"]) / 2
    return zones


# =====================================================================================
def fvg_scalp(df: pd.DataFrame, s) -> tuple[dict, dict]:
    """Craig Percoco-style scalp, adapted to MES.

    Long (shorts are the mirror image):
      1. Optional 1D bias: the previous daily candle is green.
      2. 15M: a bullish fair-value gap from the last few days is still intact.
      3. Price pulls back to the zone's midpoint (the key level).
      4. 1M change of character: price makes a pullback swing high, then a strong
         1M candle closes above that swing high and leaves a bullish 1M fair-value gap.
      5. Entry: limit order at the 1M gap's midpoint (cancelled if not filled in time).
      6. Stop: one tick below the candle that made the gap.
         Target: R x the stop distance (he aims for at least 1:4).
    A 15M zone is dropped once a 1M candle closes through its far side, once it's older
    than zone_lookback_days, or once it has produced a trade.
    """
    n = len(df)
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    times = df.index
    bias = daily_bias(df) if s.use_daily_bias else np.zeros(n, int)
    zones = fvg_zones(df, s.min_gap_points)
    lookback = pd.Timedelta(days=s.zone_lookback_days)
    touch_window = s.touch_window_bars
    tick = s.tick_size

    orders: dict[int, dict] = {}
    ztop, zmid, zbot = (np.full(n, np.nan) for _ in range(3))
    zi, active = 0, []
    swing_hi = swing_lo = None      # (price, bar index) of the latest confirmed 1M swings

    for i in range(n):
        while zi < len(zones) and zones[zi]["active_from"] <= times[i]:
            active.append(dict(zones[zi], touched=None, done=False))
            zi += 1
        active = [z for z in active if not z["done"] and times[i] - z["active_from"] <= lookback]

        # confirm a 1M swing at the previous bar
        if i >= 2:
            j = i - 1
            if h[j] > h[j - 1] and h[j] >= h[i]:
                swing_hi = (h[j], j)
            if l[j] < l[j - 1] and l[j] <= l[i]:
                swing_lo = (l[j], j)

        for z in active:
            side = z["side"]
            if side == 1:
                if c[i] < z["bottom"]:
                    z["done"] = True
                    continue
                if l[i] <= z["mid"]:
                    z["touched"] = i
            else:
                if c[i] > z["top"]:
                    z["done"] = True
                    continue
                if h[i] >= z["mid"]:
                    z["touched"] = i
            t0 = z["touched"]
            if t0 is None or i - t0 > touch_window or i < 2 or i in orders:
                continue
            if s.use_daily_bias and bias[i] != side:
                continue

            if side == 1 and swing_hi and swing_hi[1] >= t0 - 3:
                level = swing_hi[0]
                displaced = c[i - 1] > level and c[i - 1] > o[i - 1]
                gap = l[i] > h[i - 2]
                if displaced and gap:
                    orders[i] = dict(side=1, limit=snap((l[i] + h[i - 2]) / 2, tick, 1),
                                     stop=l[i - 1] - tick, expire=s.limit_expiry_bars,
                                     note="15M zone long")
                    z["done"] = True
            elif side == -1 and swing_lo and swing_lo[1] >= t0 - 3:
                level = swing_lo[0]
                displaced = c[i - 1] < level and c[i - 1] < o[i - 1]
                gap = h[i] < l[i - 2]
                if displaced and gap:
                    orders[i] = dict(side=-1, limit=snap((h[i] + l[i - 2]) / 2, tick, -1),
                                     stop=h[i - 1] + tick, expire=s.limit_expiry_bars,
                                     note="15M zone short")
                    z["done"] = True

        live = [z for z in active if not z["done"]]
        if live:
            near = min(live, key=lambda z: abs(z["mid"] - c[i]))
            ztop[i], zmid[i], zbot[i] = near["top"], near["mid"], near["bottom"]

    return orders, {"15M gap top": ztop, "15M gap mid": zmid, "15M gap bottom": zbot}


# =====================================================================================
def key_levels(df: pd.DataFrame, s) -> pd.DataFrame:
    """Support / resistance levels, one row each: price, weight, kind, and the first
    1M bar it may be used on (no peeking).
    - swing highs / lows on each timeframe in s.level_tfs, usable once the next candle on
      that timeframe has closed. Weight 2 for 15M, 1 for anything faster.
    - the previous session's high / low, usable from the session open. Weight 3."""
    rows = []
    for tf in s.level_tfs:
        bars = df.resample(tf, label="left", closed="left").agg(
            {"high": "max", "low": "min"}).dropna()
        step = pd.Timedelta(tf)
        t, hi, lo = bars.index, bars["high"].to_numpy(), bars["low"].to_numpy()
        name, w = tf.replace("min", "M"), 2 if pd.Timedelta(tf) >= pd.Timedelta("15min") else 1
        for k in range(1, len(bars) - 1):
            when = t[k + 1] + step
            if hi[k] > hi[k - 1] and hi[k] > hi[k + 1]:
                rows.append((hi[k], w, f"{name} high", when))
            if lo[k] < lo[k - 1] and lo[k] < lo[k + 1]:
                rows.append((lo[k], w, f"{name} low", when))
    sd = session_date(df.index)
    g = df.groupby(sd)
    starts = pd.Series(df.index).groupby(sd).first()
    highs, lows = g["high"].max(), g["low"].min()
    for prev, cur in zip(highs.index[:-1], highs.index[1:]):
        rows.append((highs[prev], 3, "prev day high", starts[cur]))
        rows.append((lows[prev], 3, "prev day low", starts[cur]))
    lv = pd.DataFrame(rows, columns=["price", "weight", "kind", "when"])
    lv["bar"] = df.index.searchsorted(pd.DatetimeIndex(lv["when"]))
    return lv


def choch_scalp(df: pd.DataFrame, s) -> tuple[dict, dict]:
    """A+ setups (see _choch_setups) first. If s.min_trades_per_day is set, setups that
    only pass with s.backup_trend as the trend filter are added as tier 2: the engine
    takes them only while the day has fewer than min_trades_per_day trades."""
    orders, lines = _choch_setups(df, s)
    if s.min_trades_per_day > 0 and s.backup_trend:
        backup, _ = _choch_setups(df, replace(s, trend=s.backup_trend))
        for i, o in backup.items():
            if i not in orders:
                orders[i] = dict(o, tier=2, note="backup " + o["note"])
    return orders, lines


def _choch_setups(df: pd.DataFrame, s) -> tuple[dict, dict]:
    """Craig Percoco-style: key level -> 1M change of character -> enter at the 1M FVG,
    taking only the strongest setups.

    Longs only when the trend filter (s.trend, see trend()) says bullish, shorts only when
    it says bearish; with trend="none" both are taken.

    Short (longs are the mirror image):
      1. In the last sweep_bars minutes price pushed up into resistance: its high came
         within level_near_points of one or more key levels (see key_levels).
      2. 1M change of character: a strong bearish 1M candle closes below the latest
         1M swing low and leaves a bearish 1M fair-value gap.
      3. Score the setup, using only what is known at that bar's close:
           level strength  sum of the weights of each kind of level at the high
                           (prev day 3, 15M 2, 5M 1), +0.5 per extra time the same
                           price was a swing (up to +1.5), so stacked levels score higher
           sweep           +1 if price poked through the level and closed back under it
           displacement    the change-of-character candle's body / the average 1M range
                           over the last 30 minutes, capped at 3
         Only setups scoring at least min_score are traded.
      4. Room: skip the setup if a key level (weight >= room_min_weight) sits closer
         than min_room_r x the stop distance in the trade's direction.
      5. Entry: limit at the 1M gap's midpoint (cancelled if not filled in time).
         Stop: one tick above the high of the push into the level (stop_at="swing"),
         or above the candle that made the gap (stop_at="candle"). Exits are in engine.py.
    """
    n = len(df)
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    times = df.index
    t_ns = times.asi8
    bias = trend(df, s)
    lv = key_levels(df, s)
    lv_px, lv_w, lv_bar = lv["price"].to_numpy(), lv["weight"].to_numpy(), lv["bar"].to_numpy()
    lv_ns = pd.DatetimeIndex(lv["when"]).asi8
    lookback = pd.Timedelta(days=s.zone_lookback_days).value
    rng = pd.Series(h - l).rolling(30, min_periods=10).mean().shift(1).to_numpy()
    tick, near, W = s.tick_size, s.level_near_points, s.sweep_bars

    orders: dict[int, dict] = {}
    level_line = np.full(n, np.nan)
    swing_hi = swing_lo = None
    for i in range(2, n):
        j = i - 1
        if h[j] > h[j - 1] and h[j] >= h[i]:
            swing_hi = h[j]
        if l[j] < l[j - 1] and l[j] <= l[i]:
            swing_lo = l[j]
        if i < W or np.isnan(rng[i]):
            continue
        if swing_hi is not None and c[j] > swing_hi and c[j] > o[j] and l[i] > h[i - 2]:
            side = 1
        elif swing_lo is not None and c[j] < swing_lo and c[j] < o[j] and h[i] < l[i - 2]:
            side = -1
        else:
            continue
        if bias[i] * side < 0 or (s.trend != "none" and bias[i] == 0):
            continue
        win = slice(i - W, i + 1)
        k = i - W + (int(np.argmin(l[win])) if side == 1 else int(np.argmax(h[win])))
        ext = l[k] if side == 1 else h[k]
        m = (lv_bar <= k) & (t_ns[k] - lv_ns <= lookback) & (np.abs(lv_px - ext) <= near)
        if not m.any():
            continue
        kinds_m = lv["kind"].to_numpy()[m]
        uniq = dict(zip(kinds_m, lv_w[m]))
        strength = float(sum(uniq.values())) + 0.5 * min(int(m.sum()) - len(uniq), 3)
        key = lv_px[m][np.argmax(lv_w[m])]
        swept = (ext - key) * -side > 0 and (c[k] - key) * side > 0
        disp = min(abs(c[j] - o[j]) / rng[i], 3.0)
        score = strength + swept + disp
        if score < s.min_score:
            continue
        kinds = "+".join(sorted(uniq))
        stop = (ext if s.stop_at == "swing" else (l[j] if side == 1 else h[j])) - side * tick
        limit = snap((l[i] + h[i - 2]) / 2 if side == 1 else (h[i] + l[i - 2]) / 2, tick, side)
        if s.min_room_r > 0:
            # room to the nearest key level in the way, in multiples of the stop distance
            risk = max(abs(limit - stop), s.min_stop_points)
            usable = (lv_bar <= i) & (t_ns[i] - lv_ns <= lookback) & (lv_w >= s.room_min_weight)
            ahead = (lv_px[usable] - limit) * side
            ahead = ahead[ahead > 0]
            room = ahead.min() / risk if len(ahead) else np.inf
            if room < s.min_room_r:
                continue
        if side == 1:
            orders[i] = dict(side=1, limit=snap((l[i] + h[i - 2]) / 2, tick, 1),
                             stop=stop, expire=s.limit_expiry_bars,
                             note=f"long @ {kinds} (score {score:.1f})", score=score,
                             why=dict(strength=strength, swept=bool(swept), disp=disp, kinds=kinds, ext_bar=k, trend=int(bias[i])))
        else:
            orders[i] = dict(side=-1, limit=snap((h[i] + l[i - 2]) / 2, tick, -1),
                             stop=stop, expire=s.limit_expiry_bars,
                             note=f"short @ {kinds} (score {score:.1f})", score=score,
                             why=dict(strength=strength, swept=bool(swept), disp=disp, kinds=kinds, ext_bar=k, trend=int(bias[i])))
        level_line[k:i + 1] = key

    return orders, {"Key level": level_line}

# =====================================================================================
def orb(df: pd.DataFrame, s) -> tuple[dict, dict]:
    """Comparison: opening-range breakout of the first N minutes after 8:30 CT,
    market entry, fixed stop of stop_points."""
    n = len(df)
    c, hi_a, lo_a = df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    m = minutes(df.index)
    days = np.asarray(df.index.date)
    start, end = 8 * 60 + 30, 8 * 60 + 30 + s.orb_minutes
    orders: dict[int, dict] = {}
    rh, rl = np.full(n, np.nan), np.full(n, np.nan)
    for d in np.unique(days):
        idx = np.where(days == d)[0]
        rng = idx[(m[idx] >= start) & (m[idx] < end)]
        if len(rng) == 0:
            continue
        top, bot = hi_a[rng].max(), lo_a[rng].min()
        after = idx[(m[idx] >= end) & (m[idx] < 15 * 60)]
        rh[after], rl[after] = top, bot
        for a, b in zip(after[:-1], after[1:]):
            if c[b] > top >= c[a]:
                orders[b] = dict(side=1, limit=None, stop=None, note="OR breakout")
            elif c[b] < bot <= c[a]:
                orders[b] = dict(side=-1, limit=None, stop=None, note="OR breakdown")
    return orders, {"OR high": rh, "OR low": rl}


def my_strategy(df: pd.DataFrame, s) -> tuple[dict, dict]:
    """Template to copy and edit. Example: 9/21 EMA cross, market entry, fixed stop."""
    fast = df["close"].ewm(span=9, adjust=False).mean().to_numpy()
    slow = df["close"].ewm(span=21, adjust=False).mean().to_numpy()
    up = fast > slow
    orders = {}
    for i in range(1, len(df)):
        if up[i] and not up[i - 1]:
            orders[i] = dict(side=1, limit=None, stop=None, note="EMA cross up")
        elif not up[i] and up[i - 1]:
            orders[i] = dict(side=-1, limit=None, stop=None, note="EMA cross down")
    return orders, {"EMA 9": fast, "EMA 21": slow}


STRATEGIES = {"choch_scalp": choch_scalp, "fvg_scalp": fvg_scalp, "orb": orb, "my_strategy": my_strategy}
