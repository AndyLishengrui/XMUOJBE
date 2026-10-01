#!/bin/bash
# Night boost: restart launcher in night mode (relax CPU, up to 3 workers)
# Run via cron at 22:00 (as ubuntu user, uses sudo for root ops)
#
# With auto-scaling, worker count is determined by queue depth, not fixed.
# Night mode relaxes the CPU check so workers can run during idle hours.

LOG=/opt/algo-coach/parallel.log
PIDFILE=/opt/algo-coach/parallel.pid
LOCKFILE=/opt/algo-coach/parallel.lock

exec 200>"$LOCKFILE"
flock -n 200 || { echo "$(date): night_boost already running, exiting"; exit 0; }

echo "$(date): === Night Boost: restart in night mode ===" | tee -a "$LOG"

# Kill existing workers and launcher
sudo pkill -f "python3.*worker\.py" 2>/dev/null || true
sudo pkill -f "parallel_worker" 2>/dev/null || true
sleep 3
sudo pkill -9 -f "python3.*worker\.py" 2>/dev/null || true
sudo pkill -9 -f "parallel_worker" 2>/dev/null || true

# Release the flock before backgrounding the launcher — otherwise the launcher
# inherits fd 200 and holds the lock forever, blocking every future cron restart.
exec 200>&-

# Start auto-scaling launcher in night mode
cd /opt/algo-coach
sudo nohup /usr/bin/python3 parallel_worker.py --night >> "$LOG" 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PIDFILE"

echo "$(date): Started launcher PID $NEW_PID (night mode, auto-scale 1-3)" | tee -a "$LOG"
