#!/bin/bash
# Sets up a daily cron job that runs the job search at 8:00 AM and posts to Discord.
# Run once: bash setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
LOG="$SCRIPT_DIR/logs/job_search.log"

mkdir -p "$SCRIPT_DIR/logs"

CRON_LINE="0 8 * * * cd \"$SCRIPT_DIR\" && $PYTHON run.py >> \"$LOG\" 2>&1"

# Check if already installed
if crontab -l 2>/dev/null | grep -q "Whowork/run.py"; then
    echo "Cron job already exists. To update it, run: crontab -e"
else
    # Append to existing crontab
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "Cron job installed:"
    echo "  $CRON_LINE"
    echo ""
    echo "The job search will run every day at 8:00 AM."
    echo "Logs: $LOG"
    echo ""
    echo "To remove it later: crontab -e  (then delete the line)"
    echo "To run manually now: python run.py"
fi
