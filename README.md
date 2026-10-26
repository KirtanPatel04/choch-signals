# MES Backtester

## Live signals dashboard and journal

A GitHub Action (`.github/workflows/signals.yml`) runs `signals/build_site.py` every 10 minutes and
publishes `docs/` to GitHub Pages:

- **Signals** (`docs/index.html`): A+ / A / B CHoCH signals for MES, MYM, MNQ and SOLUSD in the NY,
  Asia and London sessions, with limit / stop / target, size for your risk, result, and a confidence
  score from the last ~30 days of the same market, session and grade.
- **Journal** (`docs/journal.html`): log trades (including your own picks), see win rate and R by
  grade, export/import CSV. Saved in your browser.

Signal rules live in `signals/engine.py` and match `indicators/MES_CHoCH_Setups.pine`.
Data is Yahoo Finance 1-minute bars (can lag), so use TradingView alerts for exact timing.

Tests MES strategies on real 1-minute bars and opens a TradingView-style chart with every
trade marked, plus win rate, P&L, drawdown, and a verdict: profitable or not.

## Setup (once)

1. Install Python 3.11+ from python.org, and the **Python** extension in VS Code.
2. Open this folder in VS Code (**File → Open Folder**).
3. Open the terminal (**View → Terminal**) and run:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Press **Cmd+Shift+P → Python: Select Interpreter** and pick `.venv`.

## Run it

Easiest: open the **Run and Debug** panel (play icon in the left bar), pick one from the
dropdown, press **F5**:

- **Backtest: CHoCH scalp (default)**: key level, 1M change of character, enter at the 1M FVG
- **Backtest: 15M FVG scalp**: the original, stricter version (fewer trades)
- **Backtest: test all variations**: tries 2R/3R/4R, 1D bias on/off, breakeven on/off,
  plus an opening-range breakout for comparison, ranks them, and opens the best one

Or from the terminal:

```bash
python run.py                        # CHoCH scalp, last ~29 days, runner up to 8R, $100 risk, $500 account
python run.py --partial-r 2 --partial-frac 0.5   # take half off at 2R instead of a quarter
python run.py --partial-r 0          # no partial profit
python run.py --min-score 8          # pickier: only higher-scoring setups
python run.py --trend none           # take longs and shorts regardless of trend
python run.py --min-trades 0         # A+ setups only (default fills to 3 trades/day with backups)
python run.py --max-losses 2         # stop after 2 losses a day (default 3)
python run.py --strategy fvg_scalp   # the original 15M FVG version
python run.py --sweep                # test every variation
python run.py --r 5 --bias           # cap the runner at 5R, only trade the daily candle's direction
python run.py --exit fixed --r 2     # plain 2R target instead of the runner
python run.py --window 08:30-10:30   # change the entry window (CT, default 08:30-14:30)
python run.py --risk 50 --balance 500
python run.py --csv export.txt       # use a NinjaTrader export instead of Yahoo
python run.py --refresh              # force a fresh download
```

Data downloads from Yahoo Finance (continuous front-month `MES=F`) and is cached in `data/`
for the day. Yahoo only keeps 1-minute bars for about 30 days. For longer tests, export
1-minute MES from NinjaTrader (**Tools → Historical Data → Export**) and pass it with `--csv`.

Reports land in `reports/`: `report_<strategy>.html` (chart, stats, trade list; click a
trade to jump to it), `trades_<strategy>.csv`, and `sweep.csv`.

## The default strategy (`choch_scalp`)

1. **Trend (15M market structure):** bullish after a 15M candle closes above the latest 15M
   swing high, bearish after one closes below the latest 15M swing low. Longs only when
   bullish, shorts only when bearish, so it trades whichever way the market is going.
   Other options: `--trend vwap`, `ema` (15M 20/50), `1h` (1H EMA 20), `daily`, `none`.
2. **Key levels:** 15M and 5M swing highs/lows from the last 3 days, plus the previous
   day's high and low.
3. **Push into a level:** in the last 20 minutes price came within 1 point of one or more
   levels (resistance for shorts, support for longs).
4. **1M change of character:** a strong 1M candle closes through the latest 1M swing the
   other way and leaves a 1M fair-value gap.
5. **Score:** level strength (previous day 3, 15M 2, 5M 1, plus a bonus when several levels
   stack), +1 if price swept through the level and closed back, plus how big the
   change-of-character candle is versus recent candles. Only setups scoring at least 6
   are traded (`--min-score`). The score is computed when the setup forms, never with
   hindsight.
6. **Entry:** limit at the 1M gap's midpoint, cancelled after 10 minutes.
   **Stop:** one tick past the high (shorts) / low (longs) of the push into the level.
7. **Minimum 3 trades a day:** A+ setups are taken first. Until the day has 3 trades,
   backup setups are also taken: the same setup with the VWAP trend filter instead of 15M
   structure (`backup_trend`). Backups are marked "backup" in the trade list.
8. **Exits:** the runner described below: partial profit at 2R, the rest runs up to 8R.

## The original strategy (`fvg_scalp`)

Based on Craig Percoco's scalping videos, adapted to MES. He doesn't publish exact rules
and keeps some confirmations private, so this is a mechanical version of what he shows:

1. **1D (optional):** previous daily candle sets the direction (green = longs only). Turn on with `--bias`.
2. **15M:** fair-value gaps from the last 3 days. A gap is 3 candles where candle 1's wick
   and candle 3's wick don't overlap. The gap's **midpoint** is the key level.
3. **Pullback:** price comes back and touches the midpoint (support for longs, resistance
   for shorts).
4. **1M change of character:** price makes a pullback swing, then a strong 1M candle closes
   through that swing and leaves a 1M fair-value gap.
5. **Entry:** limit order at the 1M gap's midpoint, cancelled after 10 minutes if not filled.
6. **Stop:** one tick beyond the candle that made the 1M gap.
   **Exit (runner, default):** stop moves to entry once a 1M candle closes past the high
   (longs) or low (shorts) of the hour before entry. At 2R, a quarter of the contracts are
   closed for profit (`--partial-r`, `--partial-frac`; needs 2+ contracts) and the stop on
   the rest moves to +1R, then trails behind each new 1M swing. The target is capped at 8R
   (`--r`). Tune with `lock_at_r`, `lock_r` and `resistance_bars` in `backtester/engine.py`.
   **Exit (`--exit fixed`):** plain R target, stop to entry after a close 1R in profit.

A 15M zone is dropped when a 1M candle closes through its far side, or after it's traded once.

## Realism built in

- Orders are decided on a bar's close and fill on **later** bars only, so there's no peeking.
- Market entries and stop exits pay 1 tick of slippage. Fees are $1.50/contract round trip
  (check your NinjaTrader plan and edit `fees_per_contract_rt` in `backtester/engine.py`).
- If a bar touches both stop and target, the **stop** counts first.
- $500 account. Position size = $100 risk ÷ stop distance, capped at 10 contracts and by $50/contract margin.
  Trades whose stop would risk more than $100 on 1 contract are skipped.
- Max 5 trades and 3 losing trades per day. New trades only 8:30–15:00 CT. Everything is flattened by 3:40 CT, before the 3:45 margin cutoff.

Limits: 1-minute bars can't show the exact path inside a minute, and the continuous contract
can jump at a quarterly roll. One month is a small sample; if a variation only works on the
month you tuned it on, it probably won't work live.

## Change the settings or add your own rules

- All defaults are in the `Settings` class at the top of `backtester/engine.py`.
- Edit `my_strategy` in `backtester/strategies.py`, then run
  `python run.py --strategy my_strategy`. Return an order dict per bar
  (`side`, `limit` or `None` for market, `stop` price or `None` for the fixed `stop_points`).
