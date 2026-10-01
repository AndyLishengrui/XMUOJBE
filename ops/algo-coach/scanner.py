#!/usr/bin/env python3
"""
WA Review Scanner — finds students stuck on a problem and enqueues them for AI review.
Two strategies:
  - WA (result=-1): ≥3 wrong answers → code fix analysis
  - Partial (result=8): ≥5 partial correct → edge case / corner case analysis
Runs as a cron job. Reads XMUOJ PostgreSQL, writes to local SQLite.
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, "/opt/algo-coach")
from models import db_session

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("scanner")


def is_nighttime() -> bool:
    """Night window: 22:00 - 06:00 CST."""
    hour = datetime.now().hour
    return hour >= 22 or hour < 6


def night_label() -> str:
    return "🌙 NIGHT" if is_nighttime() else "☀️ DAY"


# === WA Strategy ===
# Day: ≥3 WA | Night: ≥2 WA (wider coverage)
MIN_WA_TRIGGER_DAY = 2
# 同一学生同一题的「冷却时间」：这段时间内不再重复出报告/推通知
# （老师 2026-09-24 定：30 分钟。否则学生每交一次新错误代码就弹一条通知，太吵）
REPORT_COOLDOWN_MINUTES = 30      # 2026-09-23 老师要求：新手多，白天也统一降到 2（夜间本来就是 2）
MIN_WA_TRIGGER_NIGHT = 2

# === Partial Strategy ===
# Same threshold as WA — Partial Accepted is just as important
# (was: Day ≥5 / Night ≥3, now equalized with WA)

# === Common ===
LOOKBACK_HOURS_DAY = 48
LOOKBACK_HOURS_NIGHT = 72      # Night: look further back
# 固定追踪的比赛：蓝桥杯集训队题库 + 剑道试炼 + 蓝桥杯DP大全 + 集训队试炼03-08
CURATED_CONTESTS = [261, 264, 265, 271, 278, 280, 325, 365, 373, 376, 377, 378, 379, 380, 381]

# Python 课的老师账号：李胜睿(andy，共享原件+公共课题库) + 六位老师各自的那一套
TEACHER_ACCOUNTS = ('andy', 'QYSong', 'DongLin', 'ZengMing', 'HJWang', 'HRChen', 'hlzeng')

# 每次扫描时动态解析（见 _target_contests）
TARGET_CONTESTS = []


def _target_contests():
    """目标比赛 = 这几位老师名下、**当前正在进行中**的实验（另加固定的集训队名单）。

    老师 2026-09-23：「服务未来所有的 python 实验」→「也就是六位老师创建的实验」
    →「不用那么多场，目前活跃的实验都提供服务就可以」。
    所以按「创建者账号 + 正在进行中」动态解析：
      · 新实验一开自动进来，实验结束自动退出 —— 不用人工维护 ID 列表
      · 要加新老师，只改上面 TEACHER_ACCOUNTS 一行
    ⚠️ 扫描本身只看最近 48/72 小时的提交，所以没人在做的场次不会产生任何报告。
    """
    global TARGET_CONTESTS
    if TARGET_CONTESTS:
        return TARGET_CONTESTS
    # 注意：固定名单**只作为 SQL 里的 OR 条件**参与（同样要满足"正在进行中"），
    # 不能无条件加进来 —— 否则已结束的集训队题库会一直被算作目标（踩过）。
    ids = []
    auto = 0
    try:
        names = ",".join("'%s'" % u for u in TEACHER_ACCOUNTS)
        curated = ",".join(str(i) for i in CURATED_CONTESTS)
        # 只要「正在进行中」的：实验一结束自动退出、新实验一开自动进来，不用人工维护。
        # ⚠️ 必须返回**两列**：pg_query 的解析规则要求 len(fields) >= 2 才认作一行，
        #    单列结果会被整段当成"续行"丢掉（踩过）。
        rows = pg_query(
            "SELECT c.id, COUNT(DISTINCT p.id) FROM contest c "
            "JOIN \"user\" u ON u.id = c.created_by_id "
            "LEFT JOIN problem p ON p.contest_id = c.id "
            "WHERE (u.username IN (%s) OR c.id IN (%s)) "
            "  AND now() >= c.start_time AND now() < c.end_time "
            "GROUP BY c.id" % (names, curated))
        for r in rows:
            if r and str(r[0]).strip().isdigit():
                ids.append(int(r[0]))
        auto = len(set(ids)) - len(set(CURATED_CONTESTS))
    except Exception as e:
        logger.warning("动态解析老师实验失败，本轮只用固定名单: %s" % e)
    TARGET_CONTESTS = sorted(set(ids))
    logger.info("目标比赛 %d 场（老师名下 / 固定名单中，当前正在进行中的实验）"
                % len(TARGET_CONTESTS))
    return TARGET_CONTESTS


def pg_query(sql: str) -> list:
    """Run a query against XMUOJ PostgreSQL. Returns list of field lists (strings).
    Handles multi-line code fields by using the first-field-is-digit heuristic."""
    cmd = [
        "sudo", "docker", "exec", "oj-postgres",
        "psql", "-U", "onlinejudge", "-d", "onlinejudge",
        "-t", "-A", "-F", "|",
        "-c", sql
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    if result.returncode != 0:
        logger.error(f"PG query failed: {result.stderr[:200] if isinstance(result.stderr, bytes) else str(result.stderr)[:200]}")
        return []

    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    rows = []
    current = None
    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        fields = line.split("|")
        # A new row starts when first field looks like a standalone value (digits or alphanumeric username)
        is_new_row = (len(fields) >= 2 and fields[0].strip() and
                      (fields[0].strip().isdigit() or
                       (fields[0].strip()[0].isdigit() and len(fields[0].strip()) >= 10)))
        if is_new_row:
            if current:
                rows.append(current)
            current = fields
        elif current is not None:
            current[-1] += "\n" + line
    if current:
        rows.append(current)
    return rows


def pg_query_simple(sql: str) -> list:
    """Run a simple PG query that returns rows as lists of fields. Uses | separator."""
    cmd = [
        "sudo", "docker", "exec", "oj-postgres",
        "psql", "-U", "onlinejudge", "-d", "onlinejudge",
        "-t", "-A", "-F", "|",
        "-c", sql
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode != 0:
        logger.error(f"PG query failed: {result.stderr[:200] if isinstance(result.stderr, bytes) else str(result.stderr)[:200]}")
        return []

    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    rows = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        fields = [f.strip() for f in line.split("|")]
        if fields:
            rows.append(fields)
    return rows


def find_stuck_students(result_code: int, trigger: int, review_type: str,
                         lookback_hours: int = 48) -> list:
    """Find students with repeated failures on the same problem, no AC."""
    _tc = _target_contests()
    contest_filter = f"AND p.contest_id IN ({','.join(map(str, _tc))})" if _tc else ""
    label = {-1: "WA", 4: "RuntimeError", 8: "Partial", -2: "CompileError"}.get(result_code, f"result{result_code}")

    sql = f"""
        WITH fail_counts AS (
            SELECT s.user_id, s.problem_id, s.username, p._id, p.title,
                   COUNT(*) as fail_cnt,
                   MAX(s.create_time) as last_fail
            FROM submission s
            JOIN problem p ON s.problem_id = p.id
            WHERE s.result = {result_code}
              AND s.create_time > NOW() - INTERVAL '{lookback_hours} hours'
              {contest_filter}
            GROUP BY s.user_id, s.problem_id, s.username, p._id, p.title
            HAVING COUNT(*) >= {trigger}
        ),
        has_ac AS (
            SELECT DISTINCT s.user_id, s.problem_id
            FROM submission s
            JOIN problem p ON s.problem_id = p.id
            WHERE s.result = 0
              {contest_filter}
        ),
        latest_fail AS (
            SELECT DISTINCT ON (s.user_id, s.problem_id)
                   s.user_id, s.problem_id, s.id as sub_id, s.code
            FROM submission s
            JOIN problem p2 ON s.problem_id = p2.id
            WHERE s.result = {result_code}
              {contest_filter.replace('p.', 'p2.')}
            ORDER BY s.user_id, s.problem_id, s.create_time DESC
        )
        SELECT fc.username, fc._id, fc.title, fc.fail_cnt,
               lf.sub_id, SUBSTRING(lf.code FROM 1 FOR 3000)
        FROM fail_counts fc
        JOIN latest_fail lf ON fc.user_id = lf.user_id AND fc.problem_id = lf.problem_id
        LEFT JOIN has_ac ac ON fc.user_id = ac.user_id AND fc.problem_id = ac.problem_id
        WHERE ac.user_id IS NULL
        ORDER BY fc.fail_cnt DESC, fc.last_fail DESC
        LIMIT 30;
    """
    rows = pg_query(sql)
    results = []
    for row in rows:
        if len(row) < 6:
            continue
        results.append({
            "username": row[0].strip(),
            "problem_id": row[1].strip(),
            "problem_title": row[2].strip(),
            "fail_count": int(row[3].strip()) if row[3].strip() else 0,
            "submission_id": row[4].strip(),
            "code": row[5] if len(row) > 5 else "",
            "review_type": review_type,
        })
    return results


def enqueue_students(students: list) -> int:
    """Add stuck students to the review queue. Skip duplicates. Returns count added."""
    added = 0
    cooled = 0
    with db_session() as db:
        for s in students:
            existing = db.execute(
                "SELECT id FROM review_reports WHERE submission_id = ? UNION "
                "SELECT id FROM review_queue WHERE submission_id = ?",
                (s["submission_id"], s["submission_id"])
            ).fetchone()
            if existing:
                continue

            # 冷却：同一学生同一题，REPORT_COOLDOWN_MINUTES 内已经出过报告就跳过
            recent = db.execute(
                "SELECT id FROM review_reports WHERE username = ? AND problem_id = ? "
                "AND created_at > ? LIMIT 1",
                (s["username"], s["problem_id"], time.time() - REPORT_COOLDOWN_MINUTES * 60)
            ).fetchone()
            if recent:
                cooled += 1
                continue

            priority = s["fail_count"]
            db.execute(
                """INSERT INTO review_queue (username, problem_id, problem_title,
                   submission_id, wa_count, code_snippet, priority, queued_at, review_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (s["username"], s["problem_id"], s["problem_title"],
                 s["submission_id"], s["fail_count"], s["code"],
                 priority, time.time(), s["review_type"])
            )
            added += 1
            logger.info(f"Enqueued [{s['review_type']}] {s['username']}/{s['problem_id']} ({s['fail_count']} fails)")
    if cooled:
        logger.info(f"  （{cooled} 条因 {REPORT_COOLDOWN_MINUTES} 分钟冷却跳过）")
    return added


def cleanup_resolved_reports() -> int:
    """Mark reports as resolved if student has since AC'd.
    Uses a single bulk PG query instead of N individual queries."""
    cleaned = 0
    _tc = _target_contests()
    contest_filter = f"AND p.contest_id IN ({','.join(map(str, _tc))})" if _tc else ""

    with db_session() as db:
        reports = db.execute(
            "SELECT DISTINCT username, problem_id FROM review_reports WHERE status = 'done'"
        ).fetchall()

        if not reports:
            return 0

        # Build a bulk query: one SELECT for all (username, problem_id) pairs at once
        # Use VALUES + JOIN to check all pairs in a single round-trip
        values_parts = []
        for r in reports:
            username = r["username"].replace("'", "''")
            problem_id = r["problem_id"].replace("'", "''")
            values_parts.append(f"('{username}', '{problem_id}')")

        # Batch in groups of 500 to avoid too-long SQL
        batch_size = 500
        for i in range(0, len(values_parts), batch_size):
            batch = values_parts[i:i + batch_size]
            values_sql = ",\n".join(batch)

            sql = f"""
                SELECT v.username, v.problem_id
                FROM (VALUES {values_sql}) AS v(username, problem_id)
                WHERE EXISTS (
                    SELECT 1 FROM submission s
                    JOIN problem p ON s.problem_id = p.id
                    WHERE s.result = 0
                      {contest_filter}
                      AND s.username = v.username
                      AND p._id = v.problem_id
                )
            """
            rows = pg_query_simple(sql)
            ac_pairs = set()
            for row in rows:
                if len(row) >= 2:
                    ac_pairs.add((row[0].strip(), row[1].strip()))

            if not ac_pairs:
                continue

            # Update SQLite for all matched pairs
            for username, problem_id in ac_pairs:
                db.execute(
                    """UPDATE review_reports SET ac_after = 1, status = 'resolved'
                       WHERE username = ? AND problem_id = ? AND status = 'done'""",
                    (username, problem_id)
                )
                db.execute(
                    "DELETE FROM review_queue WHERE username = ? AND problem_id = ?",
                    (username, problem_id)
                )
                cleaned += 1
                logger.info(f"🎉 {username} AC'd {problem_id} — resolved")

            db.commit()

    return cleaned


def main():
    total_added = 0
    force_night = "--night" in sys.argv or os.environ.get("COACH_NIGHT") == "1"
    night = force_night or is_nighttime()

    wa_trigger = MIN_WA_TRIGGER_NIGHT if night else MIN_WA_TRIGGER_DAY
    partial_trigger = wa_trigger  # Partial threshold now equals WA threshold
    lookback = LOOKBACK_HOURS_NIGHT if night else LOOKBACK_HOURS_DAY

    label = "🌙 NIGHT" if night else "☀️ DAY"
    if force_night and not is_nighttime():
        label += " (forced)"
    logger.info(f"=== {label} Scanner: WA≥{wa_trigger}, Partial≥{partial_trigger}, Lookback={lookback}h ===")

    # Strategy 1: WA scan
    logger.info(f"=== Scanning WA (≥{wa_trigger}) ===")
    wa_students = find_stuck_students(result_code=-1, trigger=wa_trigger, review_type='wa',
                                       lookback_hours=lookback)
    logger.info(f"Found {len(wa_students)} WA-stuck students")
    if wa_students:
        added = enqueue_students(wa_students)
        total_added += added
        for s in wa_students[:3]:
            logger.info(f"  WA: {s['username']}/{s['problem_id']} ({s['fail_count']}×)")

    # Strategy 2: Partial scan
    logger.info(f"=== Scanning Partial (≥{partial_trigger}) ===")
    partial_students = find_stuck_students(result_code=8, trigger=partial_trigger, review_type='partial',
                                            lookback_hours=lookback)
    logger.info(f"Found {len(partial_students)} partial-stuck students")
    if partial_students:
        added = enqueue_students(partial_students)
        total_added += added
        for s in partial_students[:3]:
            logger.info(f"  Partial: {s['username']}/{s['problem_id']} ({s['fail_count']}×)")

    # Strategy 3: Runtime Error scan (Python beginners commonly crash with uncaught exceptions:
    # TypeError/ValueError/IndexError/NameError/KeyError/EOFError — result code 4)
    logger.info(f"=== Scanning Runtime Error (≥{wa_trigger}) ===")
    re_students = find_stuck_students(result_code=4, trigger=wa_trigger, review_type='runtime_error',
                                       lookback_hours=lookback)
    logger.info(f"Found {len(re_students)} runtime-error-stuck students")
    if re_students:
        added = enqueue_students(re_students)
        total_added += added
        for s in re_students[:3]:
            logger.info(f"  RE: {s['username']}/{s['problem_id']} ({s['fail_count']}×)")

    if total_added == 0:
        logger.info("No new stuck students found")
    else:
        logger.info(f"Enqueued {total_added} new tasks")

    # Cleanup
    logger.info("=== Cleanup ===")
    resolved = cleanup_resolved_reports()
    if resolved:
        logger.info(f"Resolved {resolved} report(s)")
    else:
        logger.info("No reports to clean up")


if __name__ == "__main__":
    main()
