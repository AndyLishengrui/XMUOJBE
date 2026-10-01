#!/bin/bash
# Start XMUOJ Algorithm Coach service
# Usage: bash /opt/algo-coach/start.sh

cd /opt/algo-coach
LOGFILE="/opt/algo-coach/server.log"
PIDFILE="/opt/algo-coach/server.pid"

echo "[$(date)] Starting Algorithm Coach..." | tee -a "$LOGFILE"
python3 /opt/algo-coach/app.py >> "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
echo "PID: $(cat $PIDFILE)"
