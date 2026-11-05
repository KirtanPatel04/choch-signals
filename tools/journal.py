"""Live trade journal: every real trade from journal/trades.csv (one row per trade, times in CT) with the running
balance, wins/losses, R and $, by market and by grade. Writes reports/journal.html.

    .venv/bin/python tools/journal.py
"""
from __future__ import annotations

import csv
import calendar as cal
import html
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "journal" / "trades.csv"
DAYS = ROOT / "journal" / "days.csv"                                # days with no filled trade: signals seen and why
OUT = ROOT / "reports" / "journal.html"
SITE = ROOT / "docs" / "trades.html"                                # the GitHub Pages copy
NAMES = {"MES": "Micro S&P", "MNQ": "Micro Nasdaq", "MYM": "Micro Dow", "MGC": "Micro Gold", "SIL": "Micro Silver"}
RESULT = {"target": "Target hit", "stop": "Stopped", "breakeven": "Half at +1R, rest at entry", "closed": "Closed at 2:00 PM",
          "manual": "Closed by hand"}


def num(x: str) -> float:
    return float(x) if x not in ("", None) else 0.0


def hm(t: str) -> str:
    return datetime.strptime(t, "%H:%M").strftime("%-I:%M %p") if t else ""


def money(x: float) -> str:
    return ("+" if x > 0 else "−" if x < 0 else "") + f"${abs(x):,.0f}"


def calc(t: dict) -> dict:
    d = 1 if t["side"] == "BUY" else -1
    risk = abs(num(t["entry"]) - num(t["stop"]))
    pts = (num(t["exit"]) - num(t["entry"])) * d
    usd = pts * num(t["pv"]) * num(t["qty"]) - num(t["fees"])
    return {"r": pts / risk if risk else 0.0, "usd": usd, "risk_usd": risk * num(t["pv"]) * num(t["qty"])}


def cls(x: float) -> str:
    return "pos" if x > 0 else "neg" if x < 0 else ""


def calendar(trades: list[dict], today: date, quiet: dict[date, dict]) -> str:
    """Month grids (Mon-Fri, futures days) from the first trade's month to this month: each day's $ and R,
    a weekly total column and the month total. Days in journal/days.csv show their signal count."""
    by_day: dict[date, list[dict]] = {}
    for t in trades:
        by_day.setdefault(date.fromisoformat(t["date"]), []).append(t)
    first = min([*by_day, *quiet]) if by_day or quiet else today
    start = min([date.fromisoformat(trades[0]["date"]) if trades else today, *quiet])
    out, y, m = [], first.year, first.month
    while (y, m) <= (today.year, today.month):
        weeks = [w for w in cal.Calendar(0).monthdatescalendar(y, m) if any(d.month == m and d.weekday() < 5 for d in w)]
        mt = [t for d, ts in by_day.items() if (d.year, d.month) == (y, m) for t in ts]
        mu, mr = sum(t["usd"] for t in mt), sum(t["r"] for t in mt)
        rows = []
        for w in weeks:
            cells, wt = [], []
            for d in w[:5]:
                ts = by_day.get(d, []) if d.month == m else []
                wt += ts
                kind = "out" if d.month != m else "pre" if d < start or d > today else ""
                body = ""
                if ts:
                    u, r = sum(t["usd"] for t in ts), sum(t["r"] for t in ts)
                    kind += " " + ("win" if u > 0 else "loss" if u < 0 else "flat")
                    body = (f'<b class="{cls(u)}">{money(u)}</b><span>{r:+.1f}R · {len(ts)} trade{"s" if len(ts) > 1 else ""}</span>'
                            + "".join(f'<em>{html.escape(t["market"])} {html.escape(t["grade"])} {"L" if t["side"] == "BUY" else "S"}</em>' for t in ts))
                elif d.month == m and d in quiet:
                    q = quiet[d]
                    body = f'<b>$0</b><span>no trade · {q["signals"]} signal{"s" if q["signals"] != "1" else ""}, {q["filled"]} filled</span>'
                elif d.month == m and start <= d <= today:
                    body = '<span>no trade</span>'
                cells.append(f'<div class="day {kind}{" today" if d == today else ""}"><i>{d.day if d.month == m else ""}</i>{body}</div>')
            wu = sum(t["usd"] for t in wt)
            cells.append(f'<div class="wk"><i>Week</i><b class="{cls(wu)}">{money(wu) if wt else "–"}</b></div>')
            rows.append("".join(cells))
        out.append(f"""<section class="month"><header><h3>{date(y, m, 1):%B %Y}</h3>
<span><b class="{cls(mu)}">{money(mu)}</b> · {mr:+.1f}R · {len(mt)} trade{'s' if len(mt) != 1 else ''}</span></header>
<div class="grid"><div class="dow">Mon</div><div class="dow">Tue</div><div class="dow">Wed</div><div class="dow">Thu</div><div class="dow">Fri</div><div class="dow">Week</div>
{''.join(rows)}</div></section>""")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return "".join(reversed(out))


def main() -> None:
    trades = list(csv.DictReader(SRC.open()))
    trades.sort(key=lambda t: (t["date"], t["fill_time"] or t["signal_time"]))
    for t in trades:
        t.update(calc(t))
    start = num(trades[0]["balance_before"]) if trades else 0.0
    bal = start
    for t in trades:
        bal += t["usd"]
        t["bal"] = bal
    n = len(trades)
    wins = sum(t["usd"] > 0 for t in trades)
    net = sum(t["usd"] for t in trades)
    net_r = sum(t["r"] for t in trades)
    off = [t for t in trades if t["plan"] != "Yes"]
    quiet = {date.fromisoformat(q["date"]): q for q in csv.DictReader(DAYS.open())} if DAYS.exists() else {}
    quiet_html = "".join(
        f'<article class="card"><div class="top"><div><b>No trade</b> · {html.escape(q["signals"])} signal{"s" if q["signals"] != "1" else ""}, '
        f'{html.escape(q["filled"])} filled</div><div class="muted">{d:%a %b %-d, %Y}</div></div>'
        f'<p class="notes">{html.escape(q["notes"])}</p></article>' for d, q in sorted(quiet.items(), reverse=True))

    tiles = [("Trades", str(n)), ("Won / lost", f"{wins} / {n - wins}"),
             ("Net R", f'<span class="{cls(net_r)}">{net_r:+.1f}R</span>'),
             ("Net $", f'<span class="{cls(net)}">{money(net)}</span>'),
             ("Balance", f"${start:,.0f} → <b>${bal:,.0f}</b>"),
             ("Off-plan trades", str(len(off)))]

    def group(key: str) -> str:
        rows = []
        for k in sorted({t[key] for t in trades}):
            g = [t for t in trades if t[key] == k]
            r, u = sum(t["r"] for t in g), sum(t["usd"] for t in g)
            rows.append(f'<tr><td class="l">{html.escape(k)}</td><td>{len(g)}</td><td>{sum(t["usd"] > 0 for t in g)}</td>'
                        f'<td class="{cls(r)}">{r:+.1f}</td><td class="{cls(u)}">{money(u)}</td></tr>')
        return "".join(rows)

    cards = []
    for i, t in enumerate(reversed(trades)):
        num_ = n - i
        when = datetime.strptime(t["date"], "%Y-%m-%d").strftime("%a %b %-d, %Y")
        times = [("Signal", t["signal_time"]), ("Filled", t["fill_time"]), ("+1R half closed", t["half_time"]), ("Exit", t["exit_time"])]
        cards.append(f"""
<article class="card {'win' if t['usd'] > 0 else 'loss' if t['usd'] < 0 else ''}">
  <div class="top">
    <div><span class="tag">#{num_}</span> <b>{html.escape(t['market'])}</b> <span class="muted">{NAMES.get(t['market'], '')}</span>
      · {html.escape(t['grade'])} {'LONG' if t['side'] == 'BUY' else 'SHORT'}</div>
    <div class="muted">{when}</div>
  </div>
  <div class="big"><span class="{cls(t['usd'])}">{money(t['usd'])}</span> <span class="{cls(t['r'])}">{t['r']:+.2f}R</span>
    <span class="res">{RESULT.get(t['result'], html.escape(t['result']))}</span></div>
  <dl>
    <div><dt>Entry (limit)</dt><dd>{t['entry']}</dd></div><div><dt>Stop</dt><dd>{t['stop']}</dd></div>
    <div><dt>Target</dt><dd>{t['target']}</dd></div><div><dt>Exit</dt><dd>{t['exit']}</dd></div>
    <div><dt>Contracts</dt><dd>{t['qty']}</dd></div><div><dt>Risk</dt><dd>${t['risk_usd']:,.0f}</dd></div>
    <div><dt>Fees</dt><dd>${num(t['fees']):,.2f}</dd></div><div><dt>Followed plan</dt><dd>{html.escape(t['plan'])}</dd></div>
  </dl>
  <div class="times">{''.join(f'<span><i>{k}</i> {hm(v)} CT</span>' for k, v in times if v)}</div>
  <div class="bal">Balance ${t['bal'] - t['usd']:,.0f} → <b>${t['bal']:,.0f}</b></div>
  <p class="notes">{html.escape(t['notes'])}</p>
</article>""")

    page = f"""<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Trade Journal</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
/* layout: one column - totals, month calendar, trade cards, breakdowns */
:root {{ --bg:#f5f6f8; --panel:#ffffff; --line:#e0e3ea; --text:#151922; --muted:#5d6678; --up:#0f8a7d; --down:#d23b39;
  --upbg:#e3f4f1; --downbg:#fbe8e7; --sans:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#0b0e14; --panel:#131824; --line:#232a3a; --text:#e6e9ef; --muted:#8b93a7;
  --up:#26a69a; --down:#ef5350; --upbg:#10302d; --downbg:#3a1a1c; color-scheme:dark; }} }}
:root[data-theme="dark"] {{ --bg:#0b0e14; --panel:#131824; --line:#232a3a; --text:#e6e9ef; --muted:#8b93a7;
  --up:#26a69a; --down:#ef5350; --upbg:#10302d; --downbg:#3a1a1c; color-scheme:dark; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.45 var(--sans); }}
main {{ max-width:900px; margin:0 auto; padding:20px 16px 40px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:15px; margin:26px 0 10px; }}
.muted, .note {{ color:var(--muted); }} .note {{ font-size:12.5px; }}
.pos {{ color:var(--up); }} .neg {{ color:var(--down); }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; margin-top:16px; }}
.tile {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px 12px; }}
.tile span {{ color:var(--muted); font-size:12.5px; }} .tile div {{ font-size:19px; font-variant-numeric:tabular-nums; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-left:4px solid var(--line); border-radius:10px; padding:14px 16px; margin-bottom:12px; }}
.card.win {{ border-left-color:var(--up); }} .card.loss {{ border-left-color:var(--down); }}
.top {{ display:flex; flex-wrap:wrap; justify-content:space-between; gap:6px; }}
.tag {{ color:var(--muted); font-size:12px; }}
.big {{ font-size:24px; font-weight:600; margin:8px 0 10px; font-variant-numeric:tabular-nums; }}
.res {{ font-size:13px; font-weight:500; color:var(--muted); margin-left:6px; }}
dl {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:8px 14px; margin:0; }}
dt {{ color:var(--muted); font-size:12px; }} dd {{ margin:0; font-variant-numeric:tabular-nums; }}
.times {{ display:flex; flex-wrap:wrap; gap:6px 16px; margin-top:12px; font-size:13px; }} .times i {{ color:var(--muted); font-style:normal; }}
.bal {{ margin-top:8px; font-size:13px; }}
.notes {{ color:var(--muted); margin:10px 0 0; }}
.wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; }} th {{ color:var(--muted); font-size:12.5px; font-weight:600; }}
.l {{ text-align:left; }}
nav.site {{ font-size:13px; color:var(--muted); margin-bottom:12px; }} nav.site a {{ color:var(--muted); }}
.two {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px; }}
.two > div {{ min-width:0; }}
.big, .tile div, dd, td, .day b, .wk b {{ font-family:var(--mono); }}
.month {{ margin-bottom:18px; }}
.month header {{ display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:6px; margin-bottom:8px; }}
.month h3 {{ margin:0; font-size:17px; }} .month header span {{ color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)) minmax(0,.8fr); gap:6px; }}
.dow {{ color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.06em; padding:0 4px; }}
.day, .wk {{ min-height:84px; border:1px solid var(--line); border-radius:8px; padding:6px 7px; display:flex; flex-direction:column; gap:2px; min-width:0; background:var(--panel); }}
.day i, .wk i {{ font-style:normal; color:var(--muted); font-size:12px; }}
.day b, .wk b {{ font-size:15px; }} .day span {{ color:var(--muted); font-size:11.5px; }}
.day em {{ font-style:normal; font-size:11px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.day.win {{ background:var(--upbg); border-color:var(--up); }} .day.loss {{ background:var(--downbg); border-color:var(--down); }}
.day.out {{ background:none; border-style:dashed; opacity:.35; }} .day.pre {{ opacity:.45; }}
.day.today {{ outline:2px solid var(--text); outline-offset:1px; }}
.wk {{ background:none; justify-content:flex-end; }}
@media (max-width:520px) {{ .day, .wk {{ min-height:64px; padding:4px 5px; }} .day b, .wk b {{ font-size:12.5px; }} .day span, .day em {{ display:none; }} }}
</style><main>
<h1>Trade Journal</h1>
<div class="muted">Live trades on the $6,512 CHoCH plan · times in Chicago (CT) · {n} trade{'s' if n != 1 else ''} since {datetime.strptime(trades[0]['date'], '%Y-%m-%d').strftime('%b %-d, %Y') if trades else '-'}</div>
<div class="tiles">{''.join(f'<div class="tile"><span>{k}</span><div>{v}</div></div>' for k, v in tiles)}</div>
<h2>Calendar</h2>
{calendar(trades, date.today(), quiet)}
<h2>Trades (newest first)</h2>
{''.join(cards) or '<p class="muted">No trades yet.</p>'}
{f'<h2>Days with no trade</h2>{quiet_html}' if quiet else ''}
<div class="two">
<div><h2>By market</h2><div class="wrap"><table><thead><tr><th class="l">Market</th><th>Trades</th><th>Wins</th><th>Net R</th><th>Net $</th></tr></thead><tbody>{group('market')}</tbody></table></div></div>
<div><h2>By grade</h2><div class="wrap"><table><thead><tr><th class="l">Grade</th><th>Trades</th><th>Wins</th><th>Net R</th><th>Net $</th></tr></thead><tbody>{group('grade')}</tbody></table></div></div>
</div>
<p class="note">Real-money trades only. The Oct 30 MNQ sim trade (reports/journal_2026-10-30.csv) is not counted. Source: journal/trades.csv (trades) and journal/days.csv (days with no trade).</p>
</main>"""
    OUT.write_text(page)
    nav = '<nav class="site"><a href="./">Signals</a> · <a href="journal.html">Journal form</a> · <b>My trades</b></nav>\n'
    SITE.write_text("<!doctype html>\n<html lang=\"en\">\n" + page.replace("<main>", "<main>\n" + nav, 1) + "\n</html>\n")
    print(f"{n} trades, net {money(net)} ({net_r:+.1f}R), balance ${start:,.0f} -> ${bal:,.0f}. Wrote {OUT} and {SITE}")


if __name__ == "__main__":
    main()
