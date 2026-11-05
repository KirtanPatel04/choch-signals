# CHoCH Bot

Trades the tested $1,000 plan from TradingView (live CME data) into NinjaTrader, with you supervising.

| File | What it is |
|---|---|
| `CHoCH_Bot.pine` | The bot. Paste into TradingView's Pine editor, add to a **1-minute** chart. |
| `replay.py` | Replays the bot's rules over the saved 1-minute bars and opens the result in the browser. |
| `orders_replay.jsonl` | Every order message the bot would have sent Sep 30 - Oct 30 (written by `replay.py`). |

## The rules (same as `tools/account_study.py --groups MES+MNQ+MYM,MGC,SIL --risk-pct 15 --half-at-1r`)

- Signals 8:30 AM - 2:00 PM CT. A+ any time, A and B from 11:00.
- **Indices** (MES, MNQ, MYM) share one slot: one order or trade at a time, the first fill of the day ends the
  indices for the day. Same minute: MES, then MNQ, then MYM. **Gold** and **silver** each have their own slot.
- A limit that doesn't fill within 10 candles is cancelled and the next signal counts.
- Size: 15% of the balance / what 1 contract loses at the stop, rounded down. 0 contracts = skip (no order), but
  if that limit would have filled the slot is done for the day, like the test.
- 2+ contracts: close half (rounded down) at +1R and move the rest's stop to the entry. Target 2R. Flat at 3:40 PM.

## Setup

1. Three 1-minute charts: **MES1!** (slot "Indices"), **MGC1!** (slot "Gold"), **SIL1!** (slot "Silver"),
   each with `CHoCH Bot`. Keep "Send orders" **off** first: it only draws what it would do.
2. Pick a bridge that takes TradingView webhooks into your NinjaTrader account and supports: limit entry with
   stop + target bracket, cancel, partial exit, move stop, exit all. Map the bot's JSON to its format.
3. On each chart: create an alert on `CHoCH Bot`, condition "Any alert() function call", webhook URL = bridge.
4. Turn "Send orders" on, **on a sim account**, for 2-4 weeks. Each day compare with
   `.venv/bin/python tools/day_review.py <date> --balance <bal> --risk-pct 15`.
5. Update "Account balance" on all three charts after every trade. **A TradingView alert keeps the inputs it was
   created with**, so after changing the balance (or "Send orders"), delete and re-create that chart's alert.

## Messages

```json
{"bot":"choch","account":"","ticker":"MES1!","action":"buy","orderType":"limit","quantity":3,"limitPrice":7650,"stopLoss":7641.25,"takeProfit":7667.5,"grade":"A+"}
{"bot":"choch","account":"","ticker":"MES1!","action":"cancel"}
{"bot":"choch","account":"","ticker":"MES1!","action":"exit","quantity":1}
{"bot":"choch","account":"","ticker":"MES1!","action":"move_stop","stopLoss":7650}
{"bot":"choch","account":"","ticker":"MES1!","action":"exit_all"}
```

Stops and targets are not sent as separate orders - they ride on the entry's bracket at the broker, so the trade
is protected even if TradingView or the bridge goes down.

## Things that differ from the test

- **Fills.** The test fills a limit on a touch. Real limits can be touched without filling. `replay.py` shows
  both: Sep 30 - Oct 30 from $1,000, touch $19,359 (-39% worst drop), 1 tick through $12,166 (-37%).
- **Data.** TradingView CME bars differ a little from Yahoo's; a setup can grade differently or not appear.
- The bot follows the chart, not your broker: if the chart fills and the broker didn't (or the other way), the
  next messages can be wrong. That's what supervising is for.
- One month of data, rules picked after seeing results.
