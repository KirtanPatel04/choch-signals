# Saved data and results

Yahoo keeps 1-minute bars for only ~30 days, so everything we test on is kept here for good (free, no paid data).

- `2026-10_baseline/` — frozen on Oct 31, 2026: the bars up to Fri Oct 30 close (`bars/*.csv.gz`) and the plan's
  backtest of Sep 30 – Oct 30 (15% risk, half at +1R and stop to entry, 2R), the month before trading live:

  | Run | Start | End | Trades | Wins | Worst drop |
  |---|---|---|---|---|---|
  | indices + gold | $1,000 | $8,959 | 29 | 23 | -26% |
  | indices + gold | $5,000 | $50,929 | 29 | 23 | -28% |
  | indices + gold + silver | $1,000 | $19,359 | 42 | 33 | -39% |
  | indices + gold + silver | $5,000 | $116,674 | 42 | 33 | -39% |

  `plan_*.txt` hold every trade; `pages/` the chart pages. Compare each live month against these.
- `bars_latest/` — gzipped copy of `data/`, rewritten by `tools/daily_update.sh` after every trading day.
- `daily_update.log` — what each daily run did.

To use a frozen copy of the bars: `gunzip -c 2026-10_baseline/bars/MES_F_1m.csv.gz > ../data/MES_F_1m.csv`
