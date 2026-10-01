#!/bin/bash
#=============================================================================
# XMUOJ Health Check — 30-min interval via Claude cron
# Checks + Auto-fix: containers, nginx, backend, zombies, CPU, memory, disk
# Log: /home/ubuntu/xmuoj/scripts/health_check.log
#=============================================================================

LOG_FILE="/home/ubuntu/xmuoj/scripts/health_check.log"
ALERT_FILE="/home/ubuntu/xmuoj/scripts/health_alert.txt"
HOSTNAME=$(hostname)
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
ALERT=0
ALERT_MSG=""

log()   { echo "[$TIMESTAMP] $*" | tee -a "$LOG_FILE"; }
alert() { ALERT=1; ALERT_MSG="${ALERT_MSG}$*"$'\n'; log "⚠️  ALERT: $*"; }

log "======== Health Check Start ========"

# ─── 1. Docker Containers ────────────────────────────────────────────────

CONTAINERS=("oj-backend" "oj-postgres" "oj-redis" "judge-server" "judge-server-2" "textbook-dynamic")

for c in "${CONTAINERS[@]}"; do
    STATUS=$(sudo docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null || echo "MISSING")
    HEALTH=$(sudo docker inspect "$c" --format '{{.State.Health.Status}}' 2>/dev/null || echo "none")

    case "$STATUS" in
        running)
            if [ "$HEALTH" = "unhealthy" ]; then
                alert "Container $c: RUNNING but UNHEALTHY — attempting restart"
                sudo docker restart "$c" 2>/dev/null || true
                # Kill stuck entrypoint find if blocking
                timeout 3 sudo docker exec "$c" pkill -f "find /data/test_case" 2>/dev/null || true
            else
                log "✅ $c: $STATUS ($HEALTH)"
            fi
            ;;
        MISSING)
            alert "Container $c is MISSING"
            ;;
        *)
            alert "Container $c: $STATUS (expected running)"
            ;;
    esac
done

# ─── 2. Main Site HTTP ────────────────────────────────────────────────────

SITE_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 http://localhost/ 2>/dev/null || echo "000")
if [ "$SITE_CODE" = "000" ]; then
    alert "Main site (port 80) UNREACHABLE"
elif [ "$SITE_CODE" -ge 500 ]; then
    alert "Main site HTTP $SITE_CODE"
else
    log "✅ Main site: HTTP $SITE_CODE"
fi

# ─── 3. Nginx (inside container) ──────────────────────────────────────────

NGINX_PROCS=$(sudo docker top oj-backend 2>/dev/null | grep -c "[n]ginx" || echo 0)
if [ "$NGINX_PROCS" -lt 2 ]; then
    alert "Nginx inside container: $NGINX_PROCS processes — restarting"
    timeout 5 sudo docker exec oj-backend supervisorctl -c /app/deploy/supervisord.conf restart nginx 2>/dev/null || true
else
    log "✅ Nginx: $NGINX_PROCS processes"
fi

# ─── 4. Gunicorn ──────────────────────────────────────────────────────────

GUNI_COUNT=$(sudo docker top oj-backend 2>/dev/null | grep -c "[g]unicorn" || echo 0)
if [ "$GUNI_COUNT" -lt 2 ]; then
    alert "Gunicorn workers: $GUNI_COUNT (too few)"
else
    log "✅ Gunicorn: $GUNI_COUNT processes"
fi

# ─── 5. Supervisor process states ─────────────────────────────────────────

BAD_SUP=$(timeout 8 sudo docker exec oj-backend supervisorctl -c /app/deploy/supervisord.conf status 2>/dev/null | grep -v RUNNING || true)
if [ -n "$BAD_SUP" ]; then
    alert "Supervisor non-RUNNING: $BAD_SUP"
else
    log "✅ Supervisor: all RUNNING"
fi

# ─── 6. Zombie / Stuck processes ──────────────────────────────────────────

ZOMBIES=$(ps aux | awk '$8 ~ /Z/ {print $2, $11}')
if [ -n "$ZOMBIES" ]; then
    alert "Zombie processes: $(echo "$ZOMBIES" | wc -l)"
fi

HIGH_CPU=$(ps aux --sort=-%cpu | grep -vE '(ollama|llama-server)' | awk 'NR>1 && $3>95 {print $2, $3"%", $11}' | head -5)
if [ -n "$HIGH_CPU" ]; then
    alert "High CPU (>95%): $HIGH_CPU"
    # Auto-kill known stuck patterns
    echo "$HIGH_CPU" | grep -q "batch_expand" && sudo docker exec oj-backend pkill -9 -f batch_expand.py 2>/dev/null && log "🔧 Killed stuck batch_expand"
else
    log "✅ No high-CPU processes"
fi

STUCK_FIND=$(sudo docker top oj-backend 2>/dev/null | grep "find /data/test_case" | head -1 || true)
if [ -n "$STUCK_FIND" ]; then
    alert "Stuck entrypoint find process detected — killing"
    timeout 3 sudo docker exec oj-backend pkill -f "find /data/test_case" 2>/dev/null || true
fi

# ─── 7. Memory ────────────────────────────────────────────────────────────

MEM_AVAIL=$(free -m | awk '/Mem:/ {print $7}')
MEM_TOTAL=$(free -m | awk '/Mem:/ {print $2}')
MEM_PCT=$((MEM_AVAIL * 100 / MEM_TOTAL))
if [ "$MEM_PCT" -lt 5 ]; then
    alert "Low memory: ${MEM_AVAIL}MB available ($MEM_PCT%)"
else
    log "✅ Memory: ${MEM_AVAIL}MB available ($MEM_PCT%)"
fi

# ─── 8. Disk ──────────────────────────────────────────────────────────────

DISK_PCT=$(df -h / | awk 'NR>1 {print $5}' | sed 's/%//')
if [ "$DISK_PCT" -gt 90 ]; then
    alert "Disk: ${DISK_PCT}% used"
else
    log "✅ Disk: ${DISK_PCT}% used"
fi

# ─── 9. Judge Server Health ────────────────────────────────────────────────

for js in judge-server judge-server-2; do
    # 9a. Check gunicorn workers are running
    JS_WORKERS=$(sudo docker exec "$js" sh -c "ps | grep -c gunicorn" 2>/dev/null || echo 0)
    if [ "$JS_WORKERS" -lt 5 ]; then
        alert "$js: only $JS_WORKERS gunicorn processes — restarting"
        sudo docker restart "$js" 2>/dev/null || true
        continue
    fi

    # 9b. Check for recent "can't start new thread" errors
    JS_THREAD_ERR_RECENT=$(timeout 5 sudo docker exec "$js" sh -c \
        "grep 'can.t start new thread' /log/gunicorn.log 2>/dev/null | tail -1" 2>/dev/null || true)
    if [ -n "$JS_THREAD_ERR_RECENT" ]; then
        LAST_ERR_TIME=$(echo "$JS_THREAD_ERR_RECENT" | grep -oP '\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}' || echo "")
        if [ -n "$LAST_ERR_TIME" ]; then
            ERR_EPOCH=$(date -d "$LAST_ERR_TIME" +%s 2>/dev/null || echo 0)
            NOW_EPOCH=$(date +%s)
            if [ $((NOW_EPOCH - ERR_EPOCH)) -lt 1800 ]; then
                alert "$js: 'can't start new thread' error at $LAST_ERR_TIME — restarting"
                sudo docker restart "$js" 2>/dev/null || true
                continue
            fi
        fi
    fi

    # 9c. Check thread count hasn't blown up (container only)
    JS_THREADS=$(sudo docker exec "$js" sh -c "ps -eT 2>/dev/null | wc -l" 2>/dev/null || echo 0)
    if [ "$JS_THREADS" -gt 500 ]; then
        alert "$js: $JS_THREADS threads (possible leak) — restarting"
        sudo docker restart "$js" 2>/dev/null || true
    else
        log "✅ $js: $JS_WORKERS gunicorn workers, $JS_THREADS threads, no recent errors"
    fi
done

# 9d. Fix orphaned -2 submissions (stuck judging > 2 hours)
ORPHANED=$(sudo docker exec oj-postgres psql -U onlinejudge -d onlinejudge -t -c \
    "SELECT count(*) FROM submission WHERE result = -2 AND create_time < NOW() - INTERVAL '2 hours';" 2>/dev/null || echo 0)
ORPHANED=$(echo "$ORPHANED" | tr -d ' ')
if [ "$ORPHANED" -gt 100 ]; then
    alert "Orphaned judging submissions: $ORPHANED (>2h stuck) — resetting to pending"
    sudo docker exec oj-postgres psql -U onlinejudge -d onlinejudge -c \
        "UPDATE submission SET result = -1 WHERE result = -2 AND create_time < NOW() - INTERVAL '2 hours';" 2>/dev/null || true
    log "🔧 Reset $ORPHANED orphaned -2 submissions to -1"
else
    log "✅ Orphaned -2 submissions: $ORPHANED (threshold: 100)"
fi

# 9e. Fix stuck judge-server task_number (negative or > cpu_core*2 for long)
STUCK_TASKS=$(sudo docker exec oj-postgres psql -U onlinejudge -d onlinejudge -t -c \
    "SELECT hostname FROM judge_server WHERE task_number < 0 OR task_number > 16;" 2>/dev/null | tr -d ' ')
if [ -n "$STUCK_TASKS" ]; then
    alert "Judge server task_number out of range: $STUCK_TASKS — resetting to 0"
    sudo docker exec oj-postgres psql -U onlinejudge -d onlinejudge -c \
        "UPDATE judge_server SET task_number = 0 WHERE task_number < 0 OR task_number > 16;" 2>/dev/null || true
    log "🔧 Reset judge_server task_number to 0"
else
    log "✅ Judge server task_number: normal range"
fi

# ─── 10. Algorithm Coach ──────────────────────────────────────────────────

# ⚠️ 教练 API 现在只绑 docker0 网关 172.18.0.1（2026-10-01 收敛暴露面），
# 探活必须走这个地址；写 127.0.0.1 会永远探不到、然后每小时反复重启它。
COACH_HEALTH=$(curl -s --max-time 5 http://172.18.0.1:5000/health 2>/dev/null || echo '{"status":"down"}')
COACH_OK=$(echo "$COACH_HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('status')=='ok' else 'down')" 2>/dev/null || echo "down")

if [ "$COACH_OK" != "ok" ]; then
    alert "Algorithm Coach API DOWN — restarting"
    sudo kill $(pgrep -f "python3 app.py" 2>/dev/null) 2>/dev/null
    sleep 2
    # app.py 自己会绑 172.18.0.1:5000（不对公网开放）
    cd /opt/algo-coach && sudo nohup python3 app.py >> /opt/algo-coach/server.log 2>&1 &
    log "🔧 Restarted algo-coach app.py"
fi

# 算法教练由 parallel_worker.py（launcher）统一监管：它自己重启子进程，
# 并且只在低峰窗口 00:00-08:00 内起 worker。
# 原来这里查的是独立 worker.py，但 pgrep "python3 worker.py" 匹配不到 launcher
# 起的孩子（实际命令行是 .../worker.py --child），于是每 30 分钟就多起一个重复的
# worker 一直在跑 —— 白白和 OJ 抢资源。改为只检查 launcher。
COACH_LAUNCHER=$(pgrep -f "parallel_worker\.py" 2>/dev/null | wc -l)
if [ "$COACH_LAUNCHER" -lt 1 ]; then
    alert "Algorithm Coach Launcher DOWN — restarting"
    cd /opt/algo-coach && sudo nohup /usr/bin/python3 parallel_worker.py >> /opt/algo-coach/parallel.log 2>&1 &
    log "🔧 Restarted algo-coach launcher"
else
    log "✅ Algo Coach: launcher running (低峰窗口 00:00-08:00 内才起 worker)"
fi

# Check queue backlog
COACH_QUEUE=$(python3 -c "
import sqlite3
conn = sqlite3.connect('/opt/algo-coach/reports.db')
c = conn.cursor()
c.execute('SELECT COUNT(*), review_type FROM review_queue GROUP BY review_type')
rows = c.fetchall()
for r in rows:
    print(f'{r[1]}:{r[0]}', end=' ')
conn.close()
" 2>/dev/null || echo "N/A")
log "✅ Coach queue: $COACH_QUEUE"

# ─── 11. Problem Anomaly Detection ─────────────────────────────────────────

# Check for problems with unusually high failure rates (possible test case issues)
ANOMALY_CHECK=$(sudo docker exec oj-postgres psql -U onlinejudge -d onlinejudge -t -A -c "
WITH stats AS (
    SELECT p._id, p.title,
           COUNT(*) FILTER (WHERE s.result = 0) as ac,
           COUNT(*) FILTER (WHERE s.result = -1) as wa,
           COUNT(*) FILTER (WHERE s.result = 8) as partial,
           COUNT(*) as total
    FROM submission s
    JOIN problem p ON s.problem_id = p.id
    WHERE s.create_time > NOW() - INTERVAL '7 days'
      AND p.visible = true
      AND p.contest_id IS NULL
    GROUP BY p._id, p.title
    HAVING COUNT(*) >= 50
)
SELECT _id, title, ac, wa, partial, total,
       ROUND(ac::numeric / NULLIF(total, 0) * 100, 1) as ac_pct
FROM stats
WHERE (ac::numeric / NULLIF(total, 0)) < 0.05
ORDER BY ac_pct ASC
LIMIT 10;
" 2>/dev/null | tr -d ' ')

if [ -n "$ANOMALY_CHECK" ]; then
    while IFS='|' read -r pid title ac wa partial total ac_pct; do
        [ -z "$pid" ] && continue
        alert "Low-AC problem: $pid '$title' AC=${ac_pct}% ($ac/$total) WA=$wa Partial=$partial — investigate test cases"
        log "🔍 Anomaly: $pid AC=$ac_pct% — run: python3 /home/ubuntu/xmuoj/scripts/diagnose_problem.py $pid"
    done <<< "$ANOMALY_CHECK"
else
    log "✅ No low-AC anomaly problems detected"
fi

# ─── Report ───────────────────────────────────────────────────────────────

log "======== Health Check End ========"

if [ "$ALERT" -eq 1 ]; then
    echo "[$TIMESTAMP] HEALTH ALERT on $HOSTNAME:" > "$ALERT_FILE"
    echo "$ALERT_MSG" >> "$ALERT_FILE"
    log "🚨 $ALERT_FILE written"
fi

exit $ALERT
