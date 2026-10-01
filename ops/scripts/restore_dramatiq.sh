#!/bin/bash
#=============================================================================
# 监控 waiting_queue，清空后恢复 dramatiq 异步判题
# 由 cron 定时运行，建议每 2 分钟一次
#=============================================================================

LOG_FILE="/home/ubuntu/xmuoj/scripts/health_check.log"
QUEUE_KEY="waiting_queue"
RESTORE_FLAG="/home/ubuntu/xmuoj/scripts/.dramatiq_restored"

# 已经恢复过了就跳过
if [ -f "$RESTORE_FLAG" ]; then
    exit 0
fi

# 检查 waiting_queue 是否持续为空（连续 3 次检查，间隔 10s）
EMPTY_COUNT=0
for i in 1 2 3; do
    QUEUE_LEN=$(sudo docker exec oj-redis redis-cli -n 1 LLEN "$QUEUE_KEY" 2>/dev/null || echo "-1")
    if [ "$QUEUE_LEN" = "0" ]; then
        EMPTY_COUNT=$((EMPTY_COUNT + 1))
    fi
    [ $i -lt 3 ] && sleep 10
done

if [ "$EMPTY_COUNT" -lt 3 ]; then
    # Queue not empty yet, do nothing
    exit 0
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] waiting_queue 持续为空，恢复 dramatiq 异步判题" | tee -a "$LOG_FILE"

# 恢复 oj.py：直派 → dramatiq
sudo docker exec oj-backend sh -c "
    sed -i 's/JudgeDispatcher(submission.id, problem.id).judge()/judge_task.send(submission.id, problem.id)/' /app/submission/views/oj.py
"

# 重载 gunicorn 使改动生效
sudo docker exec oj-backend sh -c "
    supervisorctl -c /app/deploy/supervisord.conf signal HUP gunicorn
" 2>/dev/null

echo "[$TIMESTAMP] dramatiq 已恢复" | tee -a "$LOG_FILE"
touch "$RESTORE_FLAG"
