#!/usr/bin/env python3
"""
Coach Worker — processes the review queue when the judge is idle.
Checks CPU load and judge queue depth before grabbing a task.
Generates a structured review report via Ollama + DeepSeek-R1.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, "/opt/algo-coach")
from models import db_session, get_db

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5-coder:7b"  # Code-specialized model for bug fixing (upgraded from 3B)
# ⚠️ 教练 API 现在只绑 docker0 网关（2026-10-01 收敛暴露面），
# 写 127.0.0.1 会连不上。本机访问 172.18.0.1 同样走本地、不出网。
COACH_API = "http://172.18.0.1:5000/coach"

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("worker")


# ── Judge Idle Detection ──────────────────────────────────────────

def judge_queue_depth() -> int:
    """Count pending submissions in judge queue."""
    try:
        r = subprocess.run(
            ["sudo", "docker", "exec", "judge-server", "bash", "-c",
             "ls /judge/queue/ 2>/dev/null | wc -l"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
        )
        return int(r.stdout.strip() or 0)
    except Exception:
        return 999  # Assume busy if we can't check


def is_judge_idle() -> bool:
    """Judge is idle if queue is empty and CPU load is very low.
    When COACH_RELAX_CPU is set (night mode), only checks judge queue."""
    try:
        load = os.getloadavg()[0]
    except Exception:
        return False

    queue = judge_queue_depth()
    relax_cpu = os.environ.get("COACH_RELAX_CPU") == "1"

    if relax_cpu:
        # Night mode: only check judge queue, ignore CPU (Ollama contributes to load)
        idle = queue == 0
        if not idle:
            logger.debug(f"Judge busy: queue={queue}")
        return idle

    # Day mode: judge queue must be empty AND CPU not too busy
    idle = queue == 0 and load < 2.0
    if not idle:
        logger.debug(f"Judge busy: queue={queue}, load={load:.1f}")
    return idle


# ── 低峰窗口 ──────────────────────────────────────────────────────
# 老师要求：算法教练只在低峰跑，绝不和 OJ 抢资源。
# 窗口按【北京时间】小时计，默认 00:00-08:00 —— 这是实测 OJ 流量最低的时段，
# 只占全天请求的 1.2~1.6%；而 16:00-21:00 的高峰占 11~20%。
# 这只在窗口内处理队列：即使被健康检查脚本拉起来，窗口外也只是空转。
COACH_WINDOW_START = int(os.environ.get("COACH_WINDOW_START", "0"))
COACH_WINDOW_END = int(os.environ.get("COACH_WINDOW_END", "8"))


def in_coach_window() -> bool:
    """当前是否在允许跑算法教练的低峰窗口内。支持跨零点（如 23-7）。"""
    hour = time.localtime().tm_hour
    if COACH_WINDOW_START < COACH_WINDOW_END:
        return COACH_WINDOW_START <= hour < COACH_WINDOW_END
    return hour >= COACH_WINDOW_START or hour < COACH_WINDOW_END


# ── Review Generation ─────────────────────────────────────────────

SYSTEM_FIX = """你是代码调试专家。看下面的代码，找出导致 WA 的具体 bug，直接输出修复后的代码。

输出格式（严格遵守，不要多余的话）：

BUG: <具体哪一行/哪个条件有问题>
FIX:
```
<修复后的完整函数或代码块>
```
WHY: <一句话解释>
TAG: <边界条件/逻辑错误/变量混淆/初始化/循环条件/算法选错/输出格式/数组越界>"""

SYSTEM_PARTIAL = """你是算法教练。学生的代码部分正确（通过了部分测试用例），但还有边界条件或特殊情况没处理好。
请分析哪些 corner case 没考虑到，给出修复建议。

输出格式（严格遵守，不要多余的话）：

ISSUE: <具体哪个边界条件/特殊情况没处理>
FIX:
```
<修复后的代码片段>
```
WHY: <一句话解释为什么需要这样处理>
TAG: <边界条件/溢出/特殊输入/空值处理/极值/大输入/初始化>"""

SYSTEM_RUNTIME_ERROR = """你是代码调试专家。学生的 Python 代码运行时报错（Runtime Error），通常是抛出了未捕获的异常（TypeError/ValueError/IndexError/KeyError/NameError/EOFError/ZeroDivisionError 等）。
看下面的代码，定位具体抛出的异常类型和触发位置，直接输出修复后的代码。

输出格式（严格遵守，不要多余的话）：

BUG: <具体哪一行抛出什么异常>
FIX:
```
<修复后的完整代码块>
```
WHY: <一句话解释>
TAG: <类型错误/值错误/索引越界/键不存在/变量未定义/除零/输入格式/空输入>"""


def fetch_problem_context(submission_id: str) -> str:
    """取题面（描述 + 输入/输出说明 + 公开样例）喂给模型。

    原来 prompt 里**只有题目名字**，模型只能靠猜——实测两个模型都在讲"没处理负数输入"
    这类套话，而真 bug（比如钞票和硬币的浮点累积误差）根本推不出来。

    ⚠️ 只取**公开**信息：题面 + 样例。**故意不含隐藏测试用例**，
    否则报告一旦被 TA 通过发给学生，就等于泄露测试数据。
    """
    import json as _json
    sql = ("SELECT row_to_json(t) FROM ("
           " SELECT p.description, p.input_description, p.output_description, p.samples"
           " FROM submission s JOIN problem p ON p.id = s.problem_id"
           " WHERE s.id = %s LIMIT 1) t" % ("'" + submission_id.replace("'", "''") + "'"))
    try:
        r = subprocess.run(
            ["sudo", "docker", "exec", "oj-postgres", "psql", "-U", "onlinejudge",
             "-d", "onlinejudge", "-t", "-A", "-c", sql],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        raw = r.stdout.decode("utf-8", "replace").strip()
        if not raw:
            return ""
        row = _json.loads(raw)
    except Exception as e:
        logger.warning(f"取题面失败 {submission_id}: {e}")
        return ""

    def strip_html(s):
        s = re.sub(r"<br\s*/?>", "\n", s or "")
        s = re.sub(r"</(p|div|li|tr|h\d)>", "\n", s, flags=re.I)
        s = re.sub(r"<[^>]+>", "", s)
        s = s.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        return re.sub(r"\n{3,}", "\n\n", s).strip()

    parts = []
    d = strip_html(row.get("description"))
    if d: parts.append("题目描述：\n" + d[:800])
    i = strip_html(row.get("input_description"))
    if i: parts.append("输入格式：\n" + i[:400])
    o = strip_html(row.get("output_description"))
    if o: parts.append("输出格式：\n" + o[:400])
    samples = row.get("samples") or []
    if isinstance(samples, list) and samples:
        s0 = samples[0]
        if isinstance(s0, dict):
            parts.append("样例输入：\n%s\n样例输出：\n%s" %
                         (str(s0.get("input", ""))[:300], str(s0.get("output", ""))[:300]))
    return "\n\n".join(parts)


def generate_report(username: str, problem_id: str, problem_title: str,
                    code: str, fail_count: int, review_type: str = "wa",
                    submission_id: str = "") -> dict:
    """Generate a concise bug-fix report."""
    # Only include the core function/algorithm part, not boilerplate
    # Find main/logic function in the code
    core_code = code[:2000]  # Limit to keep prompt small

    # 题面上下文：没有它模型只能靠题目名字猜（见 fetch_problem_context 的说明）
    ctx = fetch_problem_context(submission_id) if submission_id else ""
    ctx_block = ("题目信息：\n" + ctx + "\n\n") if ctx else ""

    if review_type == "runtime_error":
        prompt = f"""运行错误(Runtime Error) {fail_count}次。题目: {problem_title}。

{ctx_block}代码:
```
{core_code}
```

找出导致运行错误的 bug（异常类型 + 位置），输出修复。"""
        system = SYSTEM_RUNTIME_ERROR
    elif review_type == "wa":
        prompt = f"""WA {fail_count}次。题目: {problem_title}。

{ctx_block}代码:
```
{core_code}
```

找出 bug，输出修复。"""
        system = SYSTEM_FIX
    else:  # partial
        prompt = f"""部分正确 {fail_count}次。题目: {problem_title}。

{ctx_block}代码:
```
{core_code}
```

找出没处理好的边界条件，输出修复。"""
        system = SYSTEM_PARTIAL

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "num_predict": 500,     # shorter = faster
            "temperature": 0.1,     # nearly deterministic for code
        }
    }

    start = time.time()
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=600)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        logger.error(f"Ollama failed: {e}")
        return None

    elapsed = time.time() - start
    raw = data.get("response", "")
    # Remove thinking tags
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Parse sections - simple key:value format
    sections = {
        "raw_report": cleaned,
        "bug_location": "",
        "fix_code": "",
        "explanation": "",
        "error_type": "",
    }

    for field, pattern in [
        ("bug_location", r"BUG:\s*(.+?)(?=FIX:|WHY:|TAG:|\Z)"),
        ("fix_code", r"FIX:\s*```\s*(.+?)```"),
        ("explanation", r"WHY:\s*(.+?)(?=TAG:|\Z)"),
        ("error_type", r"TAG:\s*(.+?)$"),
    ]:
        m = re.search(pattern, cleaned, re.DOTALL | re.IGNORECASE)
        if m:
            text = m.group(1).strip()
            if len(text) > 1200:
                text = text[:1197] + "..."
            sections[field] = text

    # Fallback: if no FIX: block, try to extract code between triple backticks
    if not sections["fix_code"]:
        m = re.search(r"```(\w+)?\s*\n(.*?)```", cleaned, re.DOTALL)
        if m:
            sections["fix_code"] = m.group(2).strip()

    sections["_gen_time"] = elapsed
    logger.info(f"Report generated in {elapsed:.0f}s for {username}/{problem_id}")
    return sections


# ── Notification Push ──────────────────────────────────────────────

def push_notification(username: str, problem_id: str, problem_title: str,
                      submission_id: str, code_analysis: str, hints: str,
                      common_pitfall: str) -> bool:
    """
    Push a coach report notification to the student via Django.
    Idempotent: skips if a notification for (username, problem_id) already exists.
    Passes data via stdin as JSON to avoid escaping headaches.
    Returns True if a new notification was created.
    """
    # Build the payload to send via stdin
    parts = []
    if code_analysis:
        parts.append(f"【代码分析】\n{code_analysis.strip()[:500]}")
    if hints:
        parts.append(f"【提示】\n{hints.strip()[:300]}")
    if common_pitfall:
        parts.append(f"【常见陷阱】\n{common_pitfall.strip()[:200]}")
    content = "\n\n".join(parts) if parts else "(无内容)"

    payload = json.dumps({
        "username": username,
        "problem_id": problem_id,
        "problem_title": problem_title,
        "submission_id": submission_id,
        "content": content,
    }, ensure_ascii=False)

    # Script inside Docker reads JSON from stdin, creates notification with dedup
    script = r"""
import os, sys, json
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oj.settings')
import django; django.setup()
from account.models import User
from notification.models import Notification

data = json.loads(sys.stdin.read())
username = data['username']
problem_id = data['problem_id']
problem_title = data['problem_title']
content = data['content']
submission_id = data['submission_id']

try:
    student = User.objects.get(username=username)
except User.DoesNotExist:
    print('SKIP: no_user')
    sys.exit(0)

title = f'[算法教练] {problem_id} {problem_title}'

if Notification.objects.filter(recipient=student, title=title).exists():
    print('SKIP: dup')
    sys.exit(0)

try:
    sender = User.objects.get(username='andy')
except User.DoesNotExist:
    sender = User.objects.filter(is_super_admin=True).first()

Notification.objects.create(
    sender=sender,
    recipient=student,
    title=title,
    content=content,
    link=f'/status/{submission_id}',
    is_read=False,
    is_deleted=False,
)
print('OK')
"""

    try:
        r = subprocess.run(
            ["sudo", "docker", "exec", "-i", "oj-backend", "python3", "-c", script],
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        output = r.stdout.decode().strip()
        if "OK" in output:
            logger.info(f"  🔔 Notification pushed to {username}/{problem_id}")
            return True
        elif "dup" in output:
            logger.info(f"  ⏭ Notification skipped (dup) for {username}/{problem_id}")
            return False
        elif "no_user" in output:
            logger.warning(f"  ⚠ User {username} not found, skipped notification")
            return False
        else:
            logger.warning(f"  ⚠ Notification push unexpected output: {output[:200]}")
            if r.stderr:
                logger.warning(f"  stderr: {r.stderr.decode()[:200]}")
            return False
    except Exception as e:
        logger.error(f"  ✗ Notification push failed: {e}")
        return False


# ── Main Loop ─────────────────────────────────────────────────────

def process_one() -> bool:
    """Process one item from the queue. Returns True if work was done."""
    with db_session() as db:
        # Clean stale locks (>120s) from crashed workers
        db.execute(
            "UPDATE review_queue SET locked_by=NULL, locked_at=NULL "
            "WHERE locked_by IS NOT NULL AND locked_at < ?",
            (time.time() - 120,)
        )

        # Claim next task
        task = db.execute(
            """SELECT id, username, problem_id, problem_title,
                      submission_id, wa_count, code_snippet, review_type
               FROM review_queue
               WHERE locked_by IS NULL
               ORDER BY priority DESC, queued_at
               LIMIT 1"""
        ).fetchone()

        if not task:
            logger.debug("Queue empty")
            return False

        task = dict(task)
        worker_id = f"worker-{os.getpid()}"

        # Lock it
        db.execute(
            "UPDATE review_queue SET locked_by = ?, locked_at = ? WHERE id = ?",
            (worker_id, time.time(), task["id"])
        )
        db.commit()

    # Check if student has already AC'd this problem since queuing
    # If yes, skip Ollama — no report needed
    try:
        result = subprocess.run(
            ["sudo", "docker", "exec", "oj-postgres", "psql", "-U", "onlinejudge",
             "-d", "onlinejudge", "-t", "-A", "-c",
             f"SELECT COUNT(*) FROM submission s JOIN problem p ON s.problem_id=p.id "
             f"WHERE s.username='{task['username']}' AND p._id='{task['problem_id']}' "
             f"AND s.result=0 LIMIT 1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip() == b'1':
            logger.info(f"Skipping {task['username']}/{task['problem_id']} — already AC")
            with db_session() as db:
                db.execute("DELETE FROM review_queue WHERE id = ?", (task["id"],))
                db.commit()
            return True  # did work (cleaned up)
    except Exception as e:
        logger.warning(f"AC check failed for {task['username']}/{task['problem_id']}: {e}")
        # Continue anyway — don't block queue if DB check fails

    # Generate report (outside transaction, can take minutes)
    review_type = task.get("review_type") or "wa"
    logger.info(f"Processing {task['username']}/{task['problem_id']} ({task['wa_count']} WA, type={review_type})...")
    report = generate_report(
        task["username"], task["problem_id"], task["problem_title"],
        task["code_snippet"], task["wa_count"], review_type,
        task.get("submission_id") or ""
    )

    with db_session() as db:
        now = time.time()
        if report:
            gen_time = report.get("_gen_time", 0)
            created = now - gen_time  # when the request was queued (approx)
            db.execute(
                """INSERT INTO review_reports
                   (username, problem_id, problem_title, submission_id, review_type,
                    status, code_analysis, hints, common_pitfall, created_at, processed_at)
                   VALUES (?, ?, ?, ?, ?, 'done', ?, ?, ?, ?, ?)""",
                (task["username"], task["problem_id"], task["problem_title"],
                 task["submission_id"], task.get("review_type", "wa"),
                 report.get("bug_location", "") + "\n\n---\n\n" + report.get("fix_code", ""),
                 report.get("explanation", ""),
                 report.get("error_type", ""),
                 created, now)
            )
            logger.info(f"✓ Report saved for {task['username']}/{task['problem_id']}")
            # Immediately push notification to student (idempotent — skips if already sent)
            push_notification(
                task["username"], task["problem_id"], task["problem_title"],
                task["submission_id"],
                report.get("bug_location", "") + "\n\n---\n\n" + report.get("fix_code", ""),
                report.get("explanation", ""),
                report.get("error_type", ""),
            )
        else:
            db.execute(
                """INSERT INTO review_reports
                   (username, problem_id, problem_title, submission_id,
                    status, code_analysis, created_at, processed_at)
                   VALUES (?, ?, ?, ?, 'failed', ?, ?, ?)""",
                (task["username"], task["problem_id"], task["problem_title"],
                 task["submission_id"],
                 "AI generation failed, will retry later",
                 time.time(), time.time())
            )

        # Remove from queue
        db.execute("DELETE FROM review_queue WHERE id = ?", (task["id"],))

    return True


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true",
                        help="Running as child of parallel launcher")
    args = parser.parse_args()

    worker_id = os.environ.get("COACH_WORKER_ID", os.environ.get("WORKER_ID", "?"))
    relax = os.environ.get("COACH_RELAX_CPU") == "1"
    mode = "night (relaxed CPU)" if relax else "day (strict CPU)"

    if args.child:
        logger.info(f"Child worker-{worker_id} starting ({mode})...")
    else:
        logger.info(f"Solo worker starting ({mode})...")

    paused = False
    while True:
        if not in_coach_window():
            # 低峰窗口外不处理任何任务。这里 sleep 而不是退出，
            # 因为 launcher 每 5 秒就会把退出的子进程重新拉起（会变成空转重启）。
            if not paused:
                logger.info(
                    f"低峰窗口外（北京时间 {time.localtime().tm_hour}:00，窗口 "
                    f"{COACH_WINDOW_START}:00-{COACH_WINDOW_END}:00），暂停处理队列，不占用资源")
                paused = True
            time.sleep(120)
            continue

        if paused:
            logger.info("进入低峰窗口，恢复处理队列")
            paused = False

        if is_judge_idle():
            worked = process_one()
            if not worked:
                # Queue empty, sleep longer
                time.sleep(60)
            else:
                # Did work, short sleep to let CPU cool
                time.sleep(10)
        else:
            # Judge busy, check again soon
            time.sleep(30)


if __name__ == "__main__":
    main()
