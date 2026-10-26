"""Builds a self-contained HTML report with a TradingView-style chart (lightweight-charts)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

LIB = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"


def _t(ts) -> int:
    """Chart time in seconds, shifted so the axis reads Chicago clock time."""
    ts = pd.Timestamp(ts)
    return int(ts.tz_localize(None).timestamp()) if ts.tzinfo else int(ts.timestamp())


def _num(x):
    if x is None:
        return None
    if isinstance(x, (float, np.floating)):
        return None if math.isnan(x) else (1e9 if math.isinf(x) else round(float(x), 2))
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def build_report(df: pd.DataFrame, result: dict, lines: dict, settings, strategy: str,
                 out: Path, sweep: list[dict] | None = None) -> Path:
    times = np.array([_t(x) for x in df.index])
    keep = np.r_[True, np.diff(times) > 0]          # chart needs strictly increasing times
    d = df[keep]
    tt = times[keep]

    candles = [dict(time=int(a), open=float(b), high=float(c), low=float(e), close=float(f))
               for a, b, c, e, f in zip(tt, d.open, d.high, d.low, d.close)]
    line_data = {}
    for name, arr in lines.items():
        arr = np.asarray(arr)[keep]
        line_data[name] = [dict(time=int(a)) if np.isnan(v) else dict(time=int(a), value=float(v))
                           for a, v in zip(tt, arr)]

    markers, rows, equity = [], [], [dict(time=int(tt[0]), value=settings.start_balance)]
    for k, tr in enumerate(result["trades"], 1):
        long = tr["side"] == "Long"
        win = tr["pnl"] > 0
        markers.append(dict(time=_t(tr["entry_time"]), position="belowBar" if long else "aboveBar",
                            color="#2962ff" if long else "#ff6d00",
                            shape="arrowUp" if long else "arrowDown",
                            text=f"#{k} {'LONG' if long else 'SHORT'}"))
        equity.append(dict(time=_t(tr["exit_time"]), value=round(tr["balance"], 2)))
        rows.append({k2: (str(v) if isinstance(v, pd.Timestamp) else _num(v))
                     for k2, v in tr.items()} | {"n": k, "t": _t(tr["entry_time"]),
                                                  "te": _t(tr["exit_time"])})
    markers.sort(key=lambda m: m["time"])
    # equity points must have unique, increasing times
    eq_clean = []
    for p in sorted(equity, key=lambda p: p["time"]):
        if eq_clean and eq_clean[-1]["time"] >= p["time"]:
            eq_clean[-1]["value"] = p["value"]
        else:
            eq_clean.append(p)

    data = dict(
        strategy=strategy, candles=candles, lines=line_data, markers=markers,
        equity=eq_clean, trades=rows, start=settings.start_balance,
        stats={k: _num(v) for k, v in result["stats"].items()},
        skipped=result["skipped"], settings=settings.to_dict(),
        range=[str(df.index[0])[:16], str(df.index[-1])[:16]],
        sweep=[{k: _num(v) for k, v in r.items()} for r in (sweep or [])],
    )
    html = TEMPLATE.replace("__LIB__", LIB).replace(
        "__DATA__", json.dumps(data).replace("</", "<\\/"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MES Backtest</title>
<script src="__LIB__"></script>
<style>
:root{--bg:#131722;--panel:#1e222d;--line:#2a2e39;--text:#d1d4dc;--mute:#787b86;--up:#26a69a;--down:#ef5350;--accent:#2962ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:18px;margin:0}h2{font-size:14px;margin:0 0 10px;color:var(--mute);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--mute)}
.verdict{padding:12px 20px;font-weight:600;border-bottom:1px solid var(--line)}
.good{color:var(--up)}.bad{color:var(--down)}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:1px;background:var(--line);border-bottom:1px solid var(--line)}
.stat{background:var(--bg);padding:10px 14px}.stat b{display:block;font-size:18px;font-variant-numeric:tabular-nums}.stat span{color:var(--mute);font-size:12px}
#chartwrap{position:relative;height:560px}#chart{position:absolute;inset:0}
#boxes{position:absolute;inset:0;pointer-events:none;z-index:3}
#equity{height:200px}
.legend{display:flex;gap:16px;flex-wrap:wrap;padding:8px 20px;color:var(--mute);font-size:12px;border-bottom:1px solid var(--line)}
.sw{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:-2px;margin-right:6px}
section{padding:16px 20px;border-bottom:1px solid var(--line)}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px}
th,td{padding:6px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:600;position:sticky;top:0;background:var(--bg)}
td:first-child,th:first-child,td.l,th.l{text-align:left}
tbody tr{cursor:pointer}tbody tr:hover{background:var(--panel)}
.best{background:rgba(41,98,255,.15)}
.note{color:var(--mute);font-size:12px;margin-top:10px}
</style></head><body>
<header><h1 id="title"></h1><span class="sub" id="range"></span></header>
<div class="verdict" id="verdict"></div>
<div class="stats" id="stats"></div>
<div class="legend"><span><i class="sw" style="background:rgba(38,166,154,.45)"></i>Profit zone (entry → target)</span>
<span><i class="sw" style="background:rgba(239,83,80,.45)"></i>Risk zone (entry → stop)</span>
<span><i class="sw" style="background:#fff;height:2px;vertical-align:3px"></i>Exit</span>
<span><i class="sw" style="border-top:2px dashed #f5c542;height:0;vertical-align:3px"></i>Partial profit taken</span></div>
<div id="chartwrap"><div id="chart"></div><canvas id="boxes"></canvas></div>
<section><h2>Account balance</h2><div id="equity"></div></section>
<section id="sweepSec" hidden><h2>All variations tested</h2><div class="tablewrap"><table id="sweep"></table></div>
<p class="note">Picking the best row after the fact overstates what you'd make live. Treat a variation as real only if it also holds up on a different month.</p></section>
<section><h2>Trades (click one to jump to it)</h2><div class="tablewrap"><table id="trades"></table></div>
<p class="note" id="skipped"></p></section>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const money = v => (v<0?"-$":"$")+Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:0});
const S = D.stats, cfg = D.settings;
$("title").textContent = `MES 1-min · ${D.strategy} · ${cfg.r_multiple}R · $${cfg.risk_per_trade} risk`;
$("range").textContent = `${D.range[0]} → ${D.range[1]} CT · ${S.days} trading days`;
const profitable = S.net_pnl > 0;
$("verdict").innerHTML = S.trades === 0 ? "No trades taken with these settings."
  : `<span class="${profitable?'good':'bad'}">${profitable?'Profitable':'Not profitable'}: ${money(S.net_pnl)} net over ${S.trades} trades</span>`
    + ` <span class="sub">(${money(S.avg_per_day)}/day average, after fees and slippage)</span>`
    + (S.blown ? ` <span class="bad">· Account dropped too low to keep trading on ${S.blown_at.slice(0,10)}</span>` : "");
const cards = [
  ["Net P&L", money(S.net_pnl), S.net_pnl>=0], ["End balance", money(S.end_balance)],
  ["Return", S.return_pct.toFixed(1)+"%", S.return_pct>=0], ["Trades", S.trades],
  ["Trades / day", S.trades_per_day.toFixed(1)], ["Win rate", S.win_rate.toFixed(1)+"%"],
  ["Avg R / trade", S.avg_r.toFixed(2)+"R", S.avg_r>=0], ["Profit factor", S.profit_factor>=1e9?"∞":S.profit_factor.toFixed(2)],
  ["Avg win", money(S.avg_win)], ["Avg loss", money(S.avg_loss)],
  ["Max drawdown", money(S.max_drawdown)+" ("+S.max_drawdown_pct.toFixed(0)+"%)", false],
  ["Worst losing streak", S.worst_losing_streak], ["Best day", money(S.best_day)], ["Worst day", money(S.worst_day)],
  ["Green / red days", `${S.green_days} / ${S.red_days}`], ["Days ≥ $300", S.days_300_plus], ["Fees paid", money(S.fees_paid)],
];
$("stats").innerHTML = cards.map(([k,v,g]) =>
  `<div class="stat"><span>${k}</span><b class="${g===true?'good':g===false?'bad':''}">${v}</b></div>`).join("");

const base = {layout:{background:{color:"#131722"},textColor:"#d1d4dc"},
  grid:{vertLines:{color:"#1e222d"},horzLines:{color:"#1e222d"}},
  timeScale:{timeVisible:true,secondsVisible:false,borderColor:"#2a2e39"},
  rightPriceScale:{borderColor:"#2a2e39"},crosshair:{mode:0},autoSize:true};
const chart = LightweightCharts.createChart($("chart"), base);
const candles = chart.addCandlestickSeries({upColor:"#26a69a",downColor:"#ef5350",borderVisible:false,
  wickUpColor:"#26a69a",wickDownColor:"#ef5350"});
candles.setData(D.candles);
const palette = ["#f5c542","#f5c542","#f5c542","#ab47bc","#00bcd4"];
Object.entries(D.lines).forEach(([name,pts],k) => {
  const s = chart.addLineSeries({color:palette[k%palette.length],lineWidth:1,
    lineStyle: name.includes("mid") ? 2 : 0, priceLineVisible:false,lastValueVisible:false,
    crosshairMarkerVisible:false,title:name});
  s.setData(pts);
});
candles.setMarkers(D.markers);

// TradingView-style long / short position boxes for every trade
const cv = $("boxes"), cx = cv.getContext("2d");
function drawBoxes() {
  const wrap = $("chartwrap"), dpr = window.devicePixelRatio || 1;
  const W = wrap.clientWidth, H = wrap.clientHeight;
  if (cv.width !== W*dpr || cv.height !== H*dpr) { cv.width = W*dpr; cv.height = H*dpr;
    cv.style.width = W+"px"; cv.style.height = H+"px"; }
  cx.setTransform(dpr,0,0,dpr,0,0); cx.clearRect(0,0,W,H);
  const ts = chart.timeScale(), plotW = W - chart.priceScale("right").width();
  const plotH = H - ts.height();
  cx.save(); cx.beginPath(); cx.rect(0,0,plotW,plotH); cx.clip();
  const bar = Math.max(ts.options().barSpacing, 1);
  D.trades.forEach(r => {
    let x0 = ts.timeToCoordinate(r.t), x1 = ts.timeToCoordinate(r.te);
    if (x0 === null || x1 === null) return;
    x1 = Math.max(x1 + bar/2, x0 + 6);
    if (x1 < 0 || x0 > plotW) return;
    const y = v => candles.priceToCoordinate(v);
    const ye = y(r.entry), yt = y(r.target), ys = y(r.stop), yx = y(r.exit);
    if ([ye,yt,ys,yx].some(v => v === null)) return;
    const win = r.pnl > 0;
    cx.fillStyle = "rgba(38,166,154,.22)"; cx.fillRect(x0, Math.min(ye,yt), x1-x0, Math.abs(yt-ye));
    cx.fillStyle = "rgba(239,83,80,.22)";  cx.fillRect(x0, Math.min(ye,ys), x1-x0, Math.abs(ys-ye));
    // the part of the move actually captured
    cx.fillStyle = win ? "rgba(38,166,154,.35)" : "rgba(239,83,80,.35)";
    cx.fillRect(x0, Math.min(ye,yx), x1-x0, Math.abs(yx-ye));
    cx.lineWidth = 1;
    cx.strokeStyle = "rgba(209,212,220,.9)"; cx.beginPath(); cx.moveTo(x0,ye); cx.lineTo(x1,ye); cx.stroke();
    cx.strokeStyle = "#26a69a"; cx.beginPath(); cx.moveTo(x0,yt); cx.lineTo(x1,yt); cx.stroke();
    cx.strokeStyle = "#ef5350"; cx.beginPath(); cx.moveTo(x0,ys); cx.lineTo(x1,ys); cx.stroke();
    if (r.partial !== null && r.partial !== undefined) {
      const yp = y(r.partial);
      cx.setLineDash([4,3]); cx.strokeStyle = "#f5c542";
      cx.beginPath(); cx.moveTo(x0,yp); cx.lineTo(x1,yp); cx.stroke(); cx.setLineDash([]);
    }
    cx.lineWidth = 2; cx.strokeStyle = "#ffffff";
    cx.beginPath(); cx.moveTo(x0,yx); cx.lineTo(x1,yx); cx.stroke();
    // label
    const long = r.side === "Long";
    const txt = `#${r.n} ${long?"LONG":"SHORT"} ${r.contracts}x  ${r.pnl>0?"+":"-"}$${Math.abs(r.pnl).toFixed(0)}  (${r.r>0?"+":""}${r.r.toFixed(1)}R)`;
    cx.font = "600 11px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif";
    // sit just above the entry / stop / exit, which stay on screen (the 8R target often doesn't)
    const tw = cx.measureText(txt).width + 10, ly = Math.max(Math.min(ye, ys, yx) - 18, 2);
    cx.fillStyle = win ? "#26a69a" : "#ef5350"; cx.fillRect(x0, ly, tw, 16);
    cx.fillStyle = "#fff"; cx.fillText(txt, x0 + 5, ly + 12);
  });
  cx.restore();
  requestAnimationFrame(drawBoxes);
}
requestAnimationFrame(drawBoxes);

const eq = LightweightCharts.createChart($("equity"), base);
const eqs = eq.addBaselineSeries({baseValue:{type:"price",price:D.start},
  topLineColor:"#26a69a",bottomLineColor:"#ef5350",
  topFillColor1:"rgba(38,166,154,.25)",topFillColor2:"rgba(38,166,154,0)",
  bottomFillColor1:"rgba(239,83,80,0)",bottomFillColor2:"rgba(239,83,80,.25)"});
eqs.setData(D.equity);
eq.timeScale().fitContent();

const jump = t => {chart.timeScale().setVisibleRange({from:t-90*60,to:t+120*60});
  window.scrollTo({top:$("chartwrap").offsetTop-10,behavior:"smooth"});};
if (D.trades.length) jump(D.trades[0].t); else chart.timeScale().fitContent();

const cols = [["n","#"],["entry_time","Entry (CT)"],["side","Side"],["setup","Setup"],["contracts","Qty"],
  ["entry","Entry"],["exit","Exit"],["stop_pts","Stop pts"],["points","Pts"],["r","R"],["pnl","P&L"],["reason","Exit"],["balance","Balance"]];
$("trades").innerHTML = "<thead><tr>"+cols.map(([,h])=>`<th>${h}</th>`).join("")+"</tr></thead><tbody>"+
  D.trades.map(r => `<tr data-t="${r.t}">`+cols.map(([k]) => {
    let v = r[k];
    if (k==="entry_time") v = String(v).slice(5,16);
    else if (k==="pnl"||k==="balance") v = `<span class="${k==='pnl'?(v>0?'good':'bad'):''}">${money(v)}</span>`;
    else if (k==="r") v = (v>0?"+":"")+v.toFixed(2);
    else if (typeof v==="number" && k!=="n" && k!=="contracts") v = v.toFixed(2);
    return `<td>${v}</td>`; }).join("")+"</tr>").join("")+"</tbody>";
$("trades").querySelectorAll("tbody tr").forEach(tr => tr.onclick = () => jump(+tr.dataset.t));
const sk = D.skipped;
$("skipped").textContent = `Setups not taken: ${sk["stop too wide"]} had a stop wider than your $${cfg.risk_per_trade} risk on 1 contract, `
  + `${sk["limit not filled"]} limit entries never filled, ${sk["daily limit"]} came after you'd hit your daily trade or loss limit.`;

if (D.sweep.length) {
  $("sweepSec").hidden = false;
  const sc = [["strategy","Strategy"],["r_multiple","R"],["use_daily_bias","1D bias"],["breakeven_at_r","BE at"],
    ["trades","Trades"],["win_rate","Win %"],["avg_r","Avg R"],["net_pnl","Net P&L"],["max_drawdown","Max DD"],["worst_losing_streak","Worst streak"]];
  const best = Math.max(...D.sweep.map(r=>r.net_pnl));
  $("sweep").innerHTML = "<thead><tr>"+sc.map(([,h])=>`<th>${h}</th>`).join("")+"</tr></thead><tbody>"+
    D.sweep.map(r => `<tr class="${r.net_pnl===best?'best':''}">`+sc.map(([k]) => {
      let v = r[k];
      if (k==="net_pnl"||k==="max_drawdown") v = `<span class="${v>0?'good':v<0?'bad':''}">${money(v)}</span>`;
      else if (k==="use_daily_bias") v = v ? "on" : "off";
      else if (k==="breakeven_at_r") v = v ? v+"R" : "off";
      else if (k==="win_rate") v = v.toFixed(1);
      else if (k==="avg_r") v = v.toFixed(2);
      return `<td>${v}</td>`; }).join("")+"</tr>").join("")+"</tbody>";
}
</script></body></html>
"""
