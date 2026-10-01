#!/usr/bin/env python3
"""
XMUOJ Algorithm Coach API
--------------------------
Wraps Ollama + DeepSeek-R1 7B as an algorithm coaching service.
Accessible from Docker containers via http://172.18.0.1:5000
"""

import json
import os
import time
import requests
import logging
import re
from flask import Flask, request, jsonify, Response, stream_with_context
from reset_password import reset_password as do_reset_password

app = Flask(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5-coder:7b"

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("algo-coach")

# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

SYSTEM_ASK = """你是 XMUOJ 算法教练，一名精通数据结构和算法的资深导师。
你的职责是帮助学生理解算法问题、分析解题思路，并解答他们在学习过程中遇到的疑问。

规则：
1. 使用中文回答，语言清晰易懂
2. 解释算法时要包含时间复杂度和空间复杂度分析
3. 如果学生提供了代码，请分析代码的正确性、潜在 bug 和改进建议
4. 提供代码示例时使用 C++ 或 Python
5. 回答结构：先总结问题 → 分析思路 → 详细解释 → 代码示例（如适用）"""

SYSTEM_COACH = """你是 XMUOJ 算法教练，采用苏格拉底式引导教学法。
你的目标不是直接告诉答案，而是通过提问和提示引导学生自己找到解法。

规则：
1. 绝不直接给出完整答案或 AC 代码
2. 先用 1-2 个问题引导学生思考
3. 根据学生回答逐步深入引导
4. 如果学生卡住，给出一个小的提示或类比
5. 如果学生明确要求看答案，先给思路框架，再问"你根据这个思路能写出来吗？"
6. 鼓励学生："你的思路很接近了！" "再想想边界条件？"
7. 使用中文"""

SYSTEM_CODE_REVIEW = """你是 XMUOJ 代码审查专家。学生提交了代码需要你帮忙分析。
请检查：逻辑正确性、边界条件、时间复杂度、潜在 bug、代码风格。
给出具体的修改建议，不要直接重写代码。
使用中文回答。"""

# ---------------------------------------------------------------------------
# Helper: call Ollama
# ---------------------------------------------------------------------------

def call_ollama(prompt: str, system: str, max_tokens: int = 2048, stream: bool = False):
    """Call Ollama generate API. Returns full response string or generator."""
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system,
        "stream": stream,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.7,
            "top_p": 0.9,
        }
    }
    start = time.time()
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=600, stream=stream)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Ollama call failed: {e}")
        raise

    if stream:
        def generate():
            for line in r.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line.decode())
                        yield chunk.get("response", "")
                        if chunk.get("done"):
                            elapsed = time.time() - start
                            logger.info(f"Stream done in {elapsed:.1f}s")
                            return
                    except json.JSONDecodeError:
                        continue
        return generate()
    else:
        data = r.json()
        elapsed = time.time() - start
        logger.info(f"Response in {elapsed:.1f}s, {data.get('eval_count',0)} tokens")
        return data.get("response", "")

# ---------------------------------------------------------------------------
# Helper: extract the actual answer from R1's thinking
# ---------------------------------------------------------------------------

def clean_r1_response(text: str) -> str:
    """Remove <｜end▁of▁thinking｜> = data.get("response", "") tags if present, return clean text."""
    # Remove  think  ...  think  blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Remove 思考  ...  思考  blocks (Chinese variant)
    text = re.sub(r'<思考>.*?</思考>', '', text, flags=re.DOTALL)
    return text.strip()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "model": MODEL,
        "ollama": OLLAMA_URL,
    })


# Security token for admin operations.
# 🔒 2026-10-01 收紧：原来兜底是硬编码明文 "xmuoj2026"，而这个 token 能调
# /reset_password 把任意学生密码重置成 123456 —— 端口曾对全网开放（实测 71 个
# 境外 IP 在扫），一旦被猜中就是账号接管。现改为：
#   环境变量 COACH_ADMIN_TOKEN  >  文件 /opt/algo-coach/.admin_token(600)  >  禁用
# 两个都没有时接口一律 403（fail closed），不再有可用默认值。
def _load_admin_token():
    t = os.environ.get("COACH_ADMIN_TOKEN")
    if t:
        return t.strip()
    try:
        with open("/opt/algo-coach/.admin_token", "r") as f:
            return f.read().strip()
    except (IOError, OSError):
        return None


ADMIN_TOKEN = _load_admin_token()


@app.route("/reset_password", methods=["POST"])
def reset_password_api():
    """
    Reset a student's password to '123456'.
    Body: { "username": "37120252204383", "real_name": "王荣汐"(optional), "token": "..." }

    Called remotely by local AI to reset student passwords.
    """
    data = request.get_json(force=True) or {}

    # Token check
    token = data.get("token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return jsonify({"ok": False, "error": "invalid_token"}), 403

    username = data.get("username", "").strip()
    real_name = data.get("real_name", "").strip() or None

    if not username:
        return jsonify({"ok": False, "error": "username is required"}), 400

    result = do_reset_password(username, real_name)

    if result.get("ok"):
        return jsonify(result)
    else:
        return jsonify(result), 400


@app.route("/ask", methods=["POST"])
def ask():
    """
    Direct algorithm Q&A.
    Body: { "question": "如何反转链表？", "problem": "(optional) 题目描述", "code": "(optional) 学生代码" }
    """
    data = request.get_json(force=True)
    question = data.get("question", "").strip()
    problem_desc = data.get("problem", "").strip()
    user_code = data.get("code", "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    prompt_parts = [f"学生提问：{question}"]
    if problem_desc:
        prompt_parts.append(f"\n题目描述：{problem_desc}")
    if user_code:
        prompt_parts.append(f"\n学生代码：\n```\n{user_code}\n```")
    prompt_parts.append("\n请回答学生的问题。")

    full_prompt = "\n".join(prompt_parts)
    stream = data.get("stream", False)

    try:
        result = call_ollama(full_prompt, SYSTEM_ASK, stream=stream)
    except requests.RequestException as e:
        return jsonify({"error": f"Ollama unavailable: {e}"}), 503

    if stream:
        return Response(stream_with_context(result), mimetype="text/event-stream")
    else:
        return jsonify({"response": result, "thinking": None})


@app.route("/coach", methods=["POST"])
def coach():
    """
    Guided coaching mode — guides student to find answer themselves.
    Body: { "question": "...", "problem": "(optional)", "code": "(optional)", "history": "(optional) 对话历史" }
    """
    data = request.get_json(force=True)
    question = data.get("question", "").strip()
    problem_desc = data.get("problem", "").strip()
    user_code = data.get("code", "").strip()
    history = data.get("history", "").strip()

    if not question:
        return jsonify({"error": "question is required"}), 400

    prompt_parts = []
    if history:
        prompt_parts.append(f"对话历史：\n{history}\n")
    prompt_parts.append(f"学生：{question}")
    if problem_desc:
        prompt_parts.append(f"\n题目：{problem_desc}")
    if user_code:
        prompt_parts.append(f"\n学生代码：\n```\n{user_code}\n```")
    prompt_parts.append("\n请用引导的方式帮助学生。")

    full_prompt = "\n".join(prompt_parts)
    stream = data.get("stream", False)

    try:
        result = call_ollama(full_prompt, SYSTEM_COACH, stream=stream)
    except requests.RequestException as e:
        return jsonify({"error": f"Ollama unavailable: {e}"}), 503

    if stream:
        return Response(stream_with_context(result), mimetype="text/event-stream")
    else:
        return jsonify({"response": result, "thinking": None})


@app.route("/review", methods=["POST"])
def review():
    """
    Code review — analyze student's submitted code.
    Body: { "problem": "题目描述", "code": "学生代码", "language": "(optional) cpp/python" }
    """
    data = request.get_json(force=True)
    problem_desc = data.get("problem", "").strip()
    user_code = data.get("code", "").strip()
    language = data.get("language", "cpp").strip()

    if not user_code:
        return jsonify({"error": "code is required"}), 400

    prompt_parts = [
        f"请审查以下{language}代码：\n```{language}\n{user_code}\n```"
    ]
    if problem_desc:
        prompt_parts.append(f"\n题目描述：{problem_desc}")
    prompt_parts.append("\n请给出详细分析。")

    full_prompt = "\n".join(prompt_parts)
    stream = data.get("stream", False)

    try:
        result = call_ollama(full_prompt, SYSTEM_CODE_REVIEW, stream=stream)
    except requests.RequestException as e:
        return jsonify({"error": f"Ollama unavailable: {e}"}), 503

    if stream:
        return Response(stream_with_context(result), mimetype="text/event-stream")
    else:
        return jsonify({"response": result, "thinking": None})

# ---------------------------------------------------------------------------
# Report endpoints (WA Review System)
# ---------------------------------------------------------------------------

import sqlite3
from contextlib import contextmanager

REPORT_DB = "/opt/algo-coach/reports.db"


def _report_db():
    conn = sqlite3.connect(REPORT_DB)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/reports/<username>", methods=["GET"])
def get_reports(username):
    """Get active (non-resolved) review reports for a student."""
    db = _report_db()
    rows = db.execute(
        """SELECT username, problem_id, problem_title, submission_id,
                  status, code_analysis, hints, common_pitfall,
                  created_at, processed_at, ac_after, view_count
           FROM review_reports
           WHERE username = ? AND status IN ('verified', 'done')
           ORDER BY created_at DESC""",
        (username,)
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/reports/<username>/<problem_id>", methods=["GET"])
def get_report(username, problem_id):
    """Get the latest review report for a specific student+problem."""
    db = _report_db()
    row = db.execute(
        """SELECT * FROM review_reports
           WHERE username = ? AND problem_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (username, problem_id)
    ).fetchone()
    db.close()
    if row:
        return jsonify(dict(row))
    return jsonify({"status": "not_found"}), 404


@app.route("/reports/stats", methods=["GET"])
def reports_stats():
    """Overall statistics."""
    db = _report_db()
    total = db.execute("SELECT COUNT(*) FROM review_reports WHERE status='done'").fetchone()[0]
    helped = db.execute("SELECT COUNT(*) FROM review_reports WHERE ac_after=1").fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0]
    db.close()
    return jsonify({"total_reports": total, "helped_ac": helped, "queue_pending": pending})


@app.route("/reports/<username>/<problem_id>/viewed", methods=["POST"])
def mark_viewed(username, problem_id):
    """Mark a report as viewed (increment view count)."""
    db = _report_db()
    db.execute(
        "UPDATE review_reports SET view_count = view_count + 1 "
        "WHERE username = ? AND problem_id = ?",
        (username, problem_id)
    )
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/reports/<username>/<problem_id>/feedback", methods=["POST"])
def submit_feedback(username, problem_id):
    """Record user feedback on a coach report."""
    data = request.get_json(force=True) or {}
    fb = data.get("feedback", "")
    if fb not in ("like", "dislike"):
        return jsonify({"error": "feedback must be like or dislike"}), 400
    db = _report_db()
    db.execute(
        "UPDATE review_reports SET feedback = ? "
        "WHERE username = ? AND problem_id = ?",
        (fb, username, problem_id)
    )
    db.commit()
    db.close()
    return jsonify({"ok": True, "feedback": fb})


@app.route("/reports/pending", methods=["GET"])
def pending_reports():
    """Get all unverified reports (for TA review dashboard)."""
    db = _report_db()
    rows = db.execute(
        """SELECT * FROM review_reports
           WHERE status IN ('done', 'unverified')
           ORDER BY created_at DESC"""
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/reports/<int:report_id>/approve", methods=["POST"])
def approve_report(report_id):
    """TA approves a report for student viewing."""
    data = request.get_json(force=True) or {}
    reviewer = data.get("reviewer", "TA")
    db = _report_db()
    db.execute(
        """UPDATE review_reports
           SET status = 'verified',
               common_pitfall = common_pitfall || ' (审核: ' || ? || ')'
           WHERE id = ?""",
        (reviewer, report_id)
    )
    db.commit()
    db.close()
    return jsonify({"ok": True, "id": report_id, "status": "verified"})


@app.route("/reports/<int:report_id>/reject", methods=["POST"])
def reject_report(report_id):
    """TA rejects a report (not shown to student)."""
    data = request.get_json(force=True) or {}
    reason = data.get("reason", "")
    db = _report_db()
    db.execute(
        "UPDATE review_reports SET status = 'rejected' WHERE id = ?",
        (report_id,)
    )
    db.commit()
    db.close()
    logger.info(f"Report {report_id} rejected: {reason}")
    return jsonify({"ok": True, "id": report_id, "status": "rejected"})
def mark_acked(username, problem_id):
    """Mark that the student AC'd after seeing the report."""
    db = _report_db()
    db.execute(
        "UPDATE review_reports SET ac_after = 1 "
        "WHERE username = ? AND problem_id = ?",
        (username, problem_id)
    )
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/monthly/<ym>", methods=["GET"])
@app.route("/monthly", methods=["GET"])
def monthly_report(ym=None):
    """Get the monthly coach performance report as JSON."""
    from monthly import generate_monthly
    report = generate_monthly(ym)
    return jsonify(report)


@app.route("/monthly/summary", methods=["GET"])
def monthly_summary():
    """Get a concise monthly summary (text)."""
    from monthly import generate_monthly, format_report
    report = generate_monthly()
    return jsonify({"text": format_report(report)})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 🔒 只绑 docker0 网关（172.18.0.1）：OJ 容器就是用这个地址访问它，公网访问不到。
    # 原来绑 0.0.0.0 等于把端口开到全网 —— 实测被 71 个境外 IP 持续扫描
    # （开放代理探测 azenv.net、SOCKS5 握手、TLS 打进明文端口、RDP 的 mstshash 探测）。
    # 所幸核查日志：无一境外 IP 访问成功、/reports 零次境外访问、无数据泄露。
    logger.info("Starting XMUOJ Algorithm Coach on 172.18.0.1:5000 (docker0 gateway, 不对公网开放)")
    logger.info(f"Using Ollama at {OLLAMA_URL}, model={MODEL}")
    app.run(host="172.18.0.1", port=5000, debug=False)
