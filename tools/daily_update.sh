#!/bin/bash
# Runs every weekday after the close (launchd: ~/Library/LaunchAgents/com.kirtan.mes-daily.plist).
# 1. Adds the latest Yahoo 1-minute bars of every market to data/ (Yahoo only keeps ~30 days, so this must run).
# 2. Backs the bars up to history/bars_latest/ (gzipped).
# 3. Rebuilds the all-history pages of the plan in reports/.
# Log: history/daily_update.log
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
echo "=== $(date) ==="

$PY tools/account_study.py --refresh --groups MES+MNQ+MYM,MGC,SIL,M2K,MCL,MHG,MBT,M6E \
    --no-open --out _refresh.html | grep -E "^MES bars|Downloading" || echo "refresh FAILED"
rm -f reports/_refresh.html

mkdir -p history/bars_latest
for f in data/*_F_1m.csv; do gzip -c "$f" > "history/bars_latest/$(basename "$f").gz"; done

# the live plan (indices + gold + silver) and the one without silver, every day we have data, from $1,000
$PY tools/account_study.py --groups MES+MNQ+MYM,MGC,SIL --risk-pct 15 --half-at-1r --no-open \
    --out plan_all_days.html | grep '^\$'
$PY tools/account_study.py --risk-pct 15 --half-at-1r --no-open --out plan_all_days_no_silver.html | grep '^\$'
for f in data/*_F_1m.csv; do echo "  $(basename "$f") last bar $(tail -1 "$f" | cut -c1-16)"; done
