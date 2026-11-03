#!/bin/bash
# "Show me my journal": refresh the bars, rebuild the three pages and open them as three browser tabs.
#   1. reports/journal.html             real trades (journal/trades.csv), also written to docs/trades.html
#   2. reports/plan_from_live.html      the CHoCH plan from the real start (Nov 2, 2026, $875)
#   3. reports/plan_all_days.html       the plan on all stored data (Sep 30, 2026, $1,000)
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
export PYTHONWARNINGS=ignore
PLAN="--groups MES+MNQ+MYM,MGC,SIL --risk-pct 15 --half-at-1r --no-open"

$PY tools/journal.py
echo "--- plan from the real start (Nov 2, \$875)"
$PY tools/account_study.py --refresh $PLAN --from 2026-11-02 --start-balance 875 --out plan_from_live.html 2>/dev/null \
    | grep -E "^ +[0-9]+  |^\\$"
echo "--- plan on all stored data (Sep 30, \$1,000)"
$PY tools/account_study.py $PLAN --out plan_all_days.html 2>/dev/null | grep -E "^\\$"
open reports/journal.html reports/plan_from_live.html reports/plan_all_days.html
