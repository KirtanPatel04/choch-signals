# CHoCH A+ indicator

Marks the backtester's setups on a 1-minute MES chart: price pushes into a key level, a 1M
change of character forms, and you enter with a limit order at the 1M fair-value gap.
**A+** setups have the traits that separated winners from losers in testing.

- `MES_CHoCH_Setups.pine`: TradingView (Pine Script v6)
- `MESChochSetups.cs`: NinjaTrader 8 indicator (NinjaScript)

Both versions use the same rules as `backtester/strategies.py` and only use candles that
have closed, so the marks don't repaint.

## What winners did vs losers

Every setup the scanner found from Sep 27 to Oct 23, 2026 (8:30 to 15:00 CT) was simulated
on its own, with the default exits ($200 risk, 25% off at 2R, runner to 8R). 410 of them
filled. Full data: `reports/setup_traits.csv`. To rerun: `python tools/winner_patterns.py`.

**Most setups lose on their own.** Only 24% won, and the average was −0.13R. The profit comes
from being picky about which setups to take, and from the exits: winners took the 2R partial
profit and trailed (median winner +1.6R), while 119 of the losers were scratched at breakeven
instead of taking a full loss.

| Trait | Result | What it means |
|---|---|---|
| **Strong change-of-character candle** (body ≥ 1.5× the average 1M candle) | −0.18R when weak, about 0R when strong, 33% vs 22% win rate | The clearest single sign. A weak break doesn't follow through. |
| **Level is a 15M swing high/low** | −0.09R vs −0.35R for 5M-only levels | 5M swings are too minor to hold. |
| **Level is the previous day's high/low** | **−0.45R, 15% win rate** (60 setups) | Worst level type. Price tended to break through these levels instead of reversing. |
| **Sweep** (poked through the level, closed back) | −0.24R vs −0.06R | It did worse this month, the opposite of what the usual liquidity-sweep idea says. |
| **Time of day** | 9:30–10:30 CT best (+0.09R), 12:00–1:30 CT worst (−0.30R) | Lunchtime chop. |
| **Trading with the trend** | Small effect on single setups (−0.09R vs −0.17R) | It matters more for which setups the strategy takes in sequence (see below). |
| **News** (big move at the 7:30 CT data release) | −0.04R vs −0.16R on quiet mornings | Data days moved more and setups followed through slightly better. This is a rough stand-in: there's no news calendar in the data. |
| **Day of week** | Monday best, Tuesday worst | Probably noise: only 4 of each. |

**The A+ pattern: strong candle + 15M level + not the previous day's high/low**

| | Setups | Win rate | Avg R | 1st half of month | 2nd half |
|---|---|---|---|---|---|
| All setups | 410 | 24% | −0.13 | −0.09 | −0.18 |
| Current strategy rule (15M trend + score ≥ 6) | 154 | 25% | +0.02 | +0.05 | −0.00 |
| **A+ pattern** | **70** | **36%** | **+0.14** | **+0.18** | **+0.10** |
| A+ and not 12:00–1:30 | 56 | 36% | +0.14 | +0.11 | +0.17 |

The A+ pattern held up in both halves of the month. When the strategy traded it in sequence
on a $500 account at $200 risk, it was profitable with every trend filter:

| Trend filter | Current rule (score ≥ 6) | A+ pattern |
|---|---|---|
| 15M structure | +0.41R, $500 → $3,448 | +0.30R, $500 → $1,762 |
| VWAP | +0.14R, $500 → $1,784 | +0.25R, $500 → $1,955 |
| None | −0.11R, account blown | +0.22R, $500 → $1,885 |

The current rule's $3,448 was partly luck. Its setups averaged only +0.02R when each one was
simulated separately, and it needs the 15M trend filter to work at all. The A+ pattern made
less in this month but held up in every version tested, so it's the more trustworthy signal.
It is still one month of data, so confirm it on other months before relying on it.

## TradingView

1. Open a **1-minute MES** chart (`MES1!`), with extended hours on.
2. Open **Pine Editor**, paste in `MES_CHoCH_Setups.pine`, and click **Add to chart**.
3. Alerts: right-click the chart, choose **Add alert**, set the condition to **CHoCH A+**, then
   pick **BUY**, **SELL**, or **Any alert() function call**. The last one sends the
   entry, stop and 2R in the alert message. These alerts can be sent by webhook to an
   auto-trading service.

## NinjaTrader 8 (Windows)

1. Copy `MESChochSetups.cs` into `Documents\NinjaTrader 8\bin\Custom\Indicators\`.
2. In NinjaTrader, open **New → NinjaScript Editor** and press **F5** to compile.
3. Open an **MES 1 Minute** chart with **Trading hours: CME US Index Futures ETH** and at least
   **5 days** loaded, then add the **MESChochSetups** indicator.
4. The hidden **Signal** plot is +1 for an A+ long, −1 for an A+ short and 0 otherwise. It's
   available to Market Analyzer columns, chart alerts and strategies, and the
   indicator also raises its own alert in real time.

## On the chart

TradingView version (only A+ setups, 8:30 to 15:00 CT, only in the direction of the trend).
It works on 1, 5 and 15-minute charts and picks its settings from the chart:

| Chart | Levels and trend | Level touch | CHoCH candle body | Tested (Sep 27 to Oct 23, fixed 2R exit) |
|---|---|---|---|---|
| 1M | 15M | 1 pt | 1.5x avg | 29 trades, 41% win, +0.11R avg |
| 5M | 15M | 3 pts | 1.0x avg | 11 trades, 36% win, −0.06R avg |
| 15M | 1H | 4 pts | 1.0x avg | 4 trades, 3 won, +0.90R avg (too few to trust) |

Only the 1M settings have enough trades to mean anything. 5M showed no edge and 15M barely
traded, and stops on both were about 18 to 25 points ($90 to $125 per contract).


- **BUY label** (teal, below the candle) or **SELL label** (red, above the candle) with the
  limit entry and stop price.
- **Red box:** entry to stop. **Green box:** entry to 2R (adjustable). **White line:** entry.
- Levels and trend are used for the signal but not drawn, to keep the chart clean.

NinjaTrader version: also draws the previous day high/low, 15M swing levels and a trend
background tint, and can show B setups.

The NinjaTrader version couldn't be compiled here because NinjaTrader isn't available on this
machine. If it shows an error when you add it, send me the message.
