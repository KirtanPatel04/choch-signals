"""A+ / B CHoCH signals on 1-minute bars - the same rules as indicators/MES_CHoCH_Setups.pine.

Setup: price pushes into a 15-minute swing level, then a 1-minute candle closes through the last
1-minute swing (change of character) and leaves a fair-value gap. Entry = limit at the middle of the
gap, stop = just past the push, target = 2R.
  A+ : CHoCH candle body >= 1.5x the 30-candle average range, at a 15M swing level, NOT at the
       previous day's high/low, with the 15M market-structure trend.
  A  : same, but the candle is 1.2x - 1.5x.
  B  : same, but the candle is 1.0x - 1.2x.
Point-based settings (1 pt touch, 2 pt min stop) are MES points, multiplied by `scale` for other markets.
Each signal is then followed on the next candles: limit fills within 10 candles (cancelled if a candle
opens past the stop), then stop (counted first if a candle hits both) or target, else closed at the
session's flat time. Times are America/Chicago.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TZ = "America/Chicago"
TARGET_R = 2.0
EXPIRY = 10          # candles the limit order stays working
SWEEP = 20           # candles back to find the push into the level
LOOK = pd.Timedelta(days=3)

# entry windows (minutes after midnight CT, close time of the signal candle) and the time open trades are closed
SESSIONS = {
    "NY":   dict(start=8 * 60 + 30, end=15 * 60, flat=15 * 60 + 40),
    "Asia": dict(start=19 * 60, end=23 * 60, flat=26 * 60),           # China/Hong Kong open; 26*60 = 2:00 next morning
    "London": dict(start=2 * 60, end=5 * 60, flat=7 * 60),
}


def session_date(idx: pd.DatetimeIndex):
    return (idx + pd.Timedelta(hours=7)).date          # CME day starts 17:00 CT


def resample(d: pd.DataFrame, minutes: int) -> pd.DataFrame:
    return d.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def levels(d: pd.DataFrame) -> pd.DataFrame:
    """15M swing highs/lows (kind 1, known 2 candles later) and previous-day high/low (kind 2)."""
    b = resample(d, 15)
    t, hi, lo = b.index, b.high.to_numpy(), b.low.to_numpy()
    rows = []
    for k in range(1, len(b) - 1):
        when = t[k + 1] + pd.Timedelta(minutes=15)
        if hi[k] > hi[k - 1] and hi[k] > hi[k + 1]:
            rows.append((hi[k], 1, when))
        if lo[k] < lo[k - 1] and lo[k] < lo[k + 1]:
            rows.append((lo[k], 1, when))
    sd = session_date(d.index)
    g = d.groupby(sd)
    starts = pd.Series(d.index, index=d.index).groupby(sd).first()
    H, L = g.high.max(), g.low.min()
    for p, c in zip(H.index[:-1], H.index[1:]):
        rows += [(H[p], 2, starts[c]), (L[p], 2, starts[c])]
    return pd.DataFrame(rows, columns=["px", "kind", "when"])


def structure(d: pd.DataFrame, minutes: int = 15) -> pd.Series:
    """+1 after a 15M close above the last swing high, -1 after a close below the last swing low; indexed by close time."""
    b = resample(d, minutes)
    hi, lo, cl = b.high.to_numpy(), b.low.to_numpy(), b.close.to_numpy()
    out, st, sh, sl = np.zeros(len(b), int), 0, None, None
    for k in range(2, len(b)):
        if hi[k - 1] > hi[k - 2] and hi[k - 1] >= hi[k]:
            sh = hi[k - 1]
        if lo[k - 1] < lo[k - 2] and lo[k - 1] <= lo[k]:
            sl = lo[k - 1]
        if sh is not None and cl[k] > sh:
            st = 1
        elif sl is not None and cl[k] < sl:
            st = -1
        out[k] = st
    return pd.Series(out, index=b.index + pd.Timedelta(minutes=minutes))


def _in_window(m: int, s: dict) -> bool:
    if s["start"] <= s["end"]:
        return s["start"] < m <= s["end"]
    return m > s["start"] or m <= s["end"]


def find_signals(d: pd.DataFrame, tick: float, scale: float = 1.0, sessions=("NY",)) -> list[dict]:
    """All A+ and B signals in `d` (1-minute OHLC, tz America/Chicago), each followed to its result."""
    d = d[~d.index.duplicated()].sort_index()
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = d.index
    close_t = idx + pd.Timedelta(minutes=1)
    lv = levels(d)
    lv_px, lv_k, lv_w = lv.px.to_numpy(), lv.kind.to_numpy(), pd.DatetimeIndex(lv.when)
    tr = structure(d).reindex(close_t, method="ffill").fillna(0).to_numpy()
    rng = pd.Series(h - l).rolling(30, min_periods=10).mean().shift(1).to_numpy()
    near, min_stop = 1.0 * scale, 2.0 * scale
    mins = np.asarray(close_t.hour * 60 + close_t.minute)
    out, swh, swl = [], None, None
    for i in range(2, len(d)):
        j = i - 1
        if h[j] > h[j - 1] and h[j] >= h[i]:
            swh = h[j]
        if l[j] < l[j - 1] and l[j] <= l[i]:
            swl = l[j]
        if i < SWEEP or np.isnan(rng[i]) or rng[i] <= 0:
            continue
        sess = next((n for n in sessions if _in_window(mins[i], SESSIONS[n])), None)
        if sess is None:
            continue
        if swh is not None and c[j] > swh and c[j] > o[j] and l[i] > h[i - 2]:
            side = 1
        elif swl is not None and c[j] < swl and c[j] < o[j] and h[i] < l[i - 2]:
            side = -1
        else:
            continue
        if tr[i] * side <= 0:
            continue
        disp = abs(c[j] - o[j]) / rng[i]
        if disp < 1.0:
            continue
        k = i - SWEEP + (int(np.argmin(l[i - SWEEP:i + 1])) if side == 1 else int(np.argmax(h[i - SWEEP:i + 1])))
        ext, tk = (l[k] if side == 1 else h[k]), idx[k]
        m = (lv_w <= tk) & (tk - lv_w <= LOOK) & (np.abs(lv_px - ext) <= near)
        if not (m & (lv_k == 1)).any() or (m & (lv_k == 2)).any():
            continue
        entry = (l[i] + h[i - 2]) / 2 if side == 1 else (h[i] + l[i - 2]) / 2
        entry = np.floor(entry / tick) * tick if side == 1 else np.ceil(entry / tick) * tick
        risk = max(abs(entry - (ext - side * tick)), min_stop)
        stop = entry - side * risk
        sig = dict(time=close_t[i], session=sess, tier="A+" if disp >= 1.5 else "A" if disp >= 1.2 else "B", side=side,
                   entry=float(entry), stop=float(stop), target=float(entry + side * TARGET_R * risk),
                   risk_pts=float(risk), strength=float(disp))
        sig.update(follow(d, i, sig, tick, SESSIONS[sess]))
        out.append(sig)
    return out


def follow(d: pd.DataFrame, i: int, s: dict, tick: float, sess: dict) -> dict:
    """What happened after signal candle i: status, fill/exit time, R. Stop gets 1 tick slippage."""
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx, side = d.index, s["side"]
    flat_t = s["time"].normalize() + pd.Timedelta(minutes=sess["flat"])
    if sess["start"] > sess["end"] and s["time"].hour * 60 + s["time"].minute <= sess["end"]:
        flat_t -= pd.Timedelta(days=1)                    # signal after midnight in a wrapping session
    fill = None
    for j in range(i + 1, len(d)):
        if idx[j] >= flat_t:
            if fill is None:
                return dict(status="no fill", r=None, exit_time=idx[j])
            return dict(status="closed", r=(o[j] - side * tick - e) * side / r1, fill_time=idx[fill], exit_time=idx[j])
        if fill is None:
            if j - i > EXPIRY or ((o[j] <= s["stop"]) if side == 1 else (o[j] >= s["stop"])):
                return dict(status="no fill", r=None, exit_time=idx[j])
            if (l[j] <= s["entry"]) if side == 1 else (h[j] >= s["entry"]):
                fill = j
                e = o[j] if ((o[j] < s["entry"]) if side == 1 else (o[j] > s["entry"])) else s["entry"]
                r1 = abs(e - s["stop"])
                st, tg = s["stop"], e + side * TARGET_R * r1
            else:
                continue
        if (l[j] <= st) if side == 1 else (h[j] >= st):
            gap = ((o[j] < st) if side == 1 else (o[j] > st)) and j != fill
            px = (o[j] if gap else st) - side * tick
            return dict(status="stop", r=(px - e) * side / r1, fill_time=idx[fill], exit_time=idx[j])
        if (h[j] >= tg) if side == 1 else (l[j] <= tg):
            return dict(status="target", r=TARGET_R, fill_time=idx[fill], exit_time=idx[j])
    if fill is None:
        return dict(status="waiting", r=None)
    return dict(status="open", r=(c[-1] - e) * side / r1, fill_time=idx[fill])


def one_at_a_time(sigs: list[dict], max_per_day: int = 2, tiers=("A+", "A", "B")) -> list[dict]:
    """Signals you could actually have taken: one order or trade at a time, max N fills per session day."""
    taken, busy_until, count = [], None, {}
    for s in sorted(sigs, key=lambda x: x["time"]):
        if s["tier"] not in tiers or (busy_until is not None and s["time"] < busy_until):
            continue
        day = session_date(pd.DatetimeIndex([s["time"]]))[0]
        if count.get(day, 0) >= max_per_day:
            continue
        busy_until = s.get("exit_time")
        if s["status"] in ("waiting", "open"):
            busy_until = pd.Timestamp.max.tz_localize(TZ)
        if s["r"] is not None:
            count[day] = count.get(day, 0) + 1
        taken.append(s)
    return taken
