#!/bin/bash
# Daily AI Score Tracker for Contest 362
# Runs AI detection + scoring, saves timestamped results
#
# Cron: 57 7 * * * /opt/algo-coach/ai_daily_score.sh
# Output: /home/ubuntu/ai_daily/YYYY-MM-DD/

set -e

CONTEST_ID=362
OUTDIR="/home/ubuntu/ai_daily/$(date +%Y-%m-%d)"
LOGFILE="/opt/algo-coach/ai_daily.log"
mkdir -p "$OUTDIR"

echo "[$(date)] === AI Daily Score: Contest $CONTEST_ID ===" | tee -a "$LOGFILE"

# Step 1: Copy AI detector to container
echo "[$(date)] Step 1: Deploy AI detector..." | tee -a "$LOGFILE"
sudo docker cp /home/ubuntu/ai_code_detector.py oj-backend:/app/

# Step 2: Run AI detection with export
echo "[$(date)] Step 2: Run AI detection..." | tee -a "$LOGFILE"
sudo docker exec oj-backend python3 /app/ai_code_detector.py $CONTEST_ID --top 200 --export 2>&1 | tail -30 | tee -a "$LOGFILE"

# Step 3: Copy scoring script and run
echo "[$(date)] Step 3: Run scoring calculator..." | tee -a "$LOGFILE"
sudo docker cp /home/ubuntu/ai_score_calculator.py oj-backend:/tmp/
sudo docker exec -w /app oj-backend python3 /tmp/ai_score_calculator.py $CONTEST_ID --export-csv 2>&1 | tee "$OUTDIR/score_report.txt"

# Step 4: Copy results out of container
echo "[$(date)] Step 4: Export results..." | tee -a "$LOGFILE"
sudo docker cp oj-backend:/tmp/ai_final_scores.csv "$OUTDIR/ai_scores.csv" 2>/dev/null
sudo docker cp oj-backend:/app/ai_detection_results.json "$OUTDIR/ai_detection.json" 2>/dev/null

# Step 5: Quick summary
STUDENTS=$(python3 -c "
import json
with open('$OUTDIR/ai_detection.json') as f:
    data = json.load(f)
high = sum(1 for s in data if s['ai_probability'] >= 0.65)
med = sum(1 for s in data if 0.50 <= s['ai_probability'] < 0.65)
low = sum(1 for s in data if s['ai_probability'] < 0.35)
total = len(data)
print(f'Total:{total} High(≥65%):{high} Med(50-65%):{med} Low(<35%):{low}')
" 2>/dev/null || echo "Summary unavailable")

echo "[$(date)] Quick: $STUDENTS" | tee -a "$LOGFILE"
echo "[$(date)] Results saved to $OUTDIR" | tee -a "$LOGFILE"
echo "" | tee -a "$LOGFILE"

# Link latest
ln -sfn "$OUTDIR" /home/ubuntu/ai_daily/latest
