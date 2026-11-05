"""Live watch of the plan's markets: every minute pull Yahoo 1m bars (about 10 min late), print prices, report new
plan signals today (A+ any time, A/B from 11:00 CT) and a Mac notification on each fill. Signals already there
at start are listed once without a notification. Run: PYTHONWARNINGS=ignore .venv/bin/python -u tools/live_watch.py
(--once for a single check)."""
import sys, time, subprocess, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "tools"); sys.path.insert(0, ".")
import pandas as pd, yfinance as yf
import account_study as A, signals.engine as E
from backtester.data import _clean
MKTS = ["MES", "MNQ", "MYM", "MGC", "SIL"]
base = {m: A.load(m, False) for m in MKTS}
seen_sig, seen_fill, once = set(), set(), "--once" in sys.argv
def note(msg):
    print(msg, flush=True)
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "CHoCH plan" sound name "Glass"'])
first = True
while True:
    D = {}
    for m in MKTS:
        try:
            new = _clean(yf.download(f"{m}=F", period="1d", interval="1m", progress=False, auto_adjust=False, prepost=True, timeout=20))
            d = pd.concat([base[m], new]); D[m] = d[~d.index.duplicated(keep="last")].sort_index()
        except Exception as e:
            D[m] = base[m]; print(f"{m} download failed: {e}", flush=True)
    now = pd.Timestamp.now(tz=E.TZ)
    print(f"{now:%H:%M} CT  " + "  ".join(f"{m} {D[m].close.iloc[-1]:g} ({D[m].index[-1]:%H:%M})" for m in MKTS), flush=True)
    for m in MKTS:
        d = D[m]
        for s in E.find_signals(d, A.MK[m][0], A.scale(D, m), sessions=("NY",), approach=15):
            t = s["time"]
            if (s["tier"] != "A+" and t.hour < 11) or t.date() != now.date() or not A.NY[0] < t.hour * 60 + t.minute <= A.NY[1]:
                continue
            side = "LONG" if s["side"] == 1 else "SHORT"
            key = (m, t)
            if key not in seen_sig:
                seen_sig.add(key)
                print(f"  {'(earlier) ' if first else ''}{s['tier']} {m} {side} signal {t:%H:%M}: limit {s['entry']:g} stop {s['stop']:g} target {s['target']:g} [{s['status']}]", flush=True)
            if s.get("fill_time") is not None and key not in seen_fill:
                seen_fill.add(key)
                if first: continue
                note(f"{s['tier']} FILL {m} {side} at {s['fill_time']:%H:%M} CT, entry {s['entry']:g} stop {s['stop']:g} target {s['target']:g} [{s['status']}]")
    first = False
    if once: break
    time.sleep(60)
