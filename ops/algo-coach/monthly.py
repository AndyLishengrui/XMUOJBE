#!/usr/bin/env python3
"""
Monthly Coach Report — generates a performance summary for the Algorithm Coach.
Usage: python3 monthly.py [YYYY-MM]   # defaults to current month
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from collections import Counter

sys.path.insert(0, "/opt/algo-coach")
from models import get_db, db_session

MONTHLY_DB = "/opt/algo-coach/reports.db"


def month_range(ym: str = None) -> tuple:
    """Return (start_ts, end_ts, label) for the given YYYY-MM."""
    if ym:
        year, month = map(int, ym.split("-"))
    else:
        now = datetime.now()
        year, month = now.year, now.month

    start = datetime(year, month, 1).timestamp()
    if month == 12:
        end = datetime(year + 1, 1, 1).timestamp()
    else:
        end = datetime(year, month + 1, 1).timestamp()
    label = f"{year}-{month:02d}"
    return start, end, label


def generate_monthly(ym: str = None) -> dict:
    """Generate the monthly report as a structured dict."""
    start, end, label = month_range(ym)
    db = get_db()

    # All reports created this month
    reports = db.execute(
        """SELECT username, problem_id, problem_title, status,
                  ac_after, view_count, common_pitfall, hints,
                  created_at, processed_at
           FROM review_reports
           WHERE created_at >= ? AND created_at < ?
           ORDER BY created_at""",
        (start, end)
    ).fetchall()

    # Queue items that were processed this month
    queue_done = db.execute(
        """SELECT COUNT(*) FROM review_queue
           WHERE queued_at >= ? AND queued_at < ? AND locked_by IS NOT NULL""",
        (start, end)
    ).fetchone()[0]

    db.close()

    if not reports:
        return {
            "month": label,
            "grade": "N/A", "grade_score": 0, "grade_reason": "本月无报告生成",
            "students_served": 0, "reports_generated": 0, "reports_failed": 0,
            "problems_covered": 0, "ac_after_report": 0, "help_rate_pct": 0,
            "total_views": 0, "avg_generation_seconds": 0,
            "top_error_types": [], "top_student": {}, "top_problem": {},
            "queue_processed": 0, "generated_at": time.time(),
        }

    students = set()
    problems = set()
    error_types = Counter()
    done_count = 0
    failed_count = 0
    resolved_count = 0  # AC after report
    total_views = 0
    generate_times = []  # seconds

    for r in reports:
        students.add(r["username"])
        problems.add(r["problem_id"])
        if r["status"] == "done":
            done_count += 1
        elif r["status"] == "failed":
            failed_count += 1
        if r["status"] in ("done", "resolved"):
            resolved_count += 1 if r["ac_after"] else 0
        if r["common_pitfall"]:
            error_types[r["common_pitfall"].strip()] += 1
        total_views += r["view_count"] or 0
        if r["processed_at"] and r["created_at"]:
            generate_times.append(r["processed_at"] - r["created_at"])

    # Build report card
    avg_gen_time = sum(generate_times) / len(generate_times) if generate_times else 0
    help_rate = (resolved_count / done_count * 100) if done_count > 0 else 0

    # Grade the coach (A-F)
    grade = grade_coach(
        students_served=len(students),
        reports=done_count,
        help_rate=help_rate,
        avg_time=avg_gen_time,
    )

    report = {
        "month": label,
        "grade": grade["letter"],
        "grade_score": grade["score"],
        "grade_reason": grade["reason"],
        "students_served": len(students),
        "reports_generated": done_count,
        "reports_failed": failed_count,
        "problems_covered": len(problems),
        "ac_after_report": resolved_count,
        "help_rate_pct": round(help_rate, 1),
        "total_views": total_views,
        "avg_generation_seconds": round(avg_gen_time, 0),
        "top_error_types": error_types.most_common(5),
        "top_student": _get_top_student(reports),
        "top_problem": _get_top_problem(reports),
        "queue_processed": queue_done,
        "generated_at": time.time(),
    }

    return report


def grade_coach(students_served: int, reports: int, help_rate: float, avg_time: float) -> dict:
    """Grade the coach on a 0-100 scale, letter grade A-F."""
    score = 0

    # Dimension 1: Volume (0-30 points)
    # How many students did we help?
    if students_served >= 30:
        score += 30
    elif students_served >= 15:
        score += 22
    elif students_served >= 5:
        score += 15
    elif students_served >= 1:
        score += 8

    # Dimension 2: Effectiveness (0-35 points)
    # How many students AC'd after getting help?
    if help_rate >= 50:
        score += 35
    elif help_rate >= 30:
        score += 28
    elif help_rate >= 15:
        score += 20
    elif help_rate >= 5:
        score += 10
    else:
        score += 3  # at least tried

    # Dimension 3: Responsiveness (0-15 points)
    # How fast were reports generated?
    if avg_time <= 120:
        score += 15
    elif avg_time <= 240:
        score += 10
    elif avg_time <= 360:
        score += 5

    # Dimension 4: Reliability (0-20 points)
    # Did reports actually get processed?
    if reports >= 20:
        score += 20
    elif reports >= 10:
        score += 15
    elif reports >= 3:
        score += 10
    elif reports >= 1:
        score += 5

    # Letter grade
    if score >= 85:
        letter = "A"
    elif score >= 70:
        letter = "B"
    elif score >= 55:
        letter = "C"
    elif score >= 40:
        letter = "D"
    else:
        letter = "F"

    reasons = []
    if students_served < 5:
        reasons.append("覆盖学生太少")
    if help_rate < 15:
        reasons.append("AC转化率偏低，建议优化提示质量")
    if avg_time > 300:
        reasons.append("报告生成过慢，建议升级硬件或精简prompt")
    if reports < 3:
        reasons.append("报告数量不足，无法评估")
    if not reasons:
        reasons.append("表现优秀")

    return {"letter": letter, "score": score, "reason": "; ".join(reasons)}


def _get_top_student(reports: list) -> dict:
    """Find the student who used the coach most this month."""
    counter = Counter()
    for r in reports:
        counter[r["username"]] += 1
    if counter:
        name, count = counter.most_common(1)[0]
        # Check if they AC'd
        ac_count = sum(1 for r in reports if r["username"] == name and r["ac_after"])
        return {"username": name, "reports": count, "ac_count": ac_count}
    return {}


def _get_top_problem(reports: list) -> dict:
    """Most reported problem this month."""
    counter = Counter()
    for r in reports:
        counter[(r["problem_id"], r["problem_title"])] += 1
    if counter:
        (pid, title), count = counter.most_common(1)[0]
        ac_count = sum(1 for r in reports if r["problem_id"] == pid and r["ac_after"])
        return {"problem_id": pid, "title": title, "reports": count, "helped_ac": ac_count}
    return {}


def format_report(report: dict) -> str:
    """Render the monthly report as a readable text."""
    if report.get("reports_generated", 0) + report.get("reports_failed", 0) == 0:
        return f"📋 {report['month']} 月报：本月无报告生成。"

    return f"""\
═══════════════════════════════════════
  XMUOJ 算法教练 — {report['month']} 月度报告
═══════════════════════════════════════

  📊 综合评分: {report['grade']} ({report['grade_score']}/100)
  📝 评语: {report['grade_reason']}

  ── 工作成果 ──
  👤 服务学生:  {report['students_served']} 人
  📄 生成报告:  {report['reports_generated']} 份
  📝 覆盖题目:  {report['problems_covered']} 道
  🎯 AC 转化率: {report['help_rate_pct']}% ({report['ac_after_report']}人)
  👁️  报告阅览: {report['total_views']} 次
  ⏱️  平均生成: {report['avg_generation_seconds']} 秒/份

  ── 高频错误类型 ──"""

    lines = [f"  {i+1}. {err_type} ({count}次)"
             for i, (err_type, count) in enumerate(report.get("top_error_types", []))]

    if report.get("top_student"):
        s = report["top_student"]
        lines.append(f"\n  ── 最活跃学生 ──")
        lines.append(f"  {s['username']}: {s['reports']}份报告, {s['ac_count']}次AC")

    if report.get("top_problem"):
        p = report["top_problem"]
        lines.append(f"\n  ── 最难题目 ──")
        lines.append(f"  {p['problem_id']} {p['title']}: {p['reports']}份报告, {p['helped_ac']}人因此AC")

    lines.append("")
    return "\n".join(lines)


def main():
    ym = sys.argv[1] if len(sys.argv) > 1 else None
    report = generate_monthly(ym)
    print(format_report(report))
    # Also save as JSON
    label = report.get("month", "unknown")
    with open(f"/opt/algo-coach/monthly-{label}.json", "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"JSON saved to /opt/algo-coach/monthly-{label}.json")


if __name__ == "__main__":
    main()
