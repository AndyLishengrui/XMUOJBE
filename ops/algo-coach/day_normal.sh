#!/bin/bash
# Day normal: restart launcher in day mode (strict CPU, up to 2 workers)
# Run via cron at 06:00 (as ubuntu user, uses sudo for root ops)
#
# With auto-scaling, worker count is determined by queue depth, not fixed.
# Day mode enforces strict CPU check before processing.

LOG=/opt/algo-coach/parallel.log
PIDFILE=/opt/algo-coach/parallel.pid
LOCKFILE=/opt/algo-coach/parallel.lock

exec 200>"$LOCKFILE"
flock -n 200 || { echo "$(date): day_normal already running, exiting"; exit 0; }

echo "$(date): === Day Normal: restart in day mode ===" | tee -a "$LOG"

# Kill existing workers and launcher
sudo pkill -f "python3.*worker\.py" 2>/dev/null || true
sudo pkill -f "parallel_worker" 2>/dev/null || true
sleep 3
sudo pkill -9 -f "python3.*worker\.py" 2>/dev/null || true
sudo pkill -9 -f "parallel_worker" 2>/dev/null || true

# Release the flock before backgrounding the launcher — otherwise the launcher
# inherits fd 200 and holds the lock forever, blocking every future cron restart.
exec 200>&-

# Start auto-scaling launcher (day mode by default)
cd /opt/algo-coach
sudo nohup /usr/bin/python3 parallel_worker.py >> "$LOG" 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PIDFILE"

echo "$(date): Started launcher PID $NEW_PID (day mode, auto-scale 1-2)" | tee -a "$LOG"
