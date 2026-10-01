#!/usr/bin/env python3
"""
Fix Verifier — submits AI-fixed code to the judge and checks if it AC's.
Only verified (AC) fixes are published to students.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, "/opt/algo-coach")
from models import db_session

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("verifier")


def extract_fix_code(report_text: str) -> str:
    """Extract the fixed code from a report. Returns '' if not extractable."""
    # Look for code between ```cpp ... ``` or ``` ... ```
    m = re.search(r"```(?:cpp|c\+\+)?\s*\n(.*?)```", report_text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Alternative: look for FIX: section
    m = re.search(r"FIX:\s*```\s*\n?(.*?)```", report_text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def submit_to_judge(problem_id: str, code: str, language: str = "cpp") -> dict:
    """
    Submit code to XMUOJ judge and wait for result.
    Uses the internal Django API.
    Returns {"verdict": "AC"|"WA"|"TLE"|..., "details": "..."}
    """
    # Write code to a temp file the judge can access
    tmpfile = f"/tmp/judge_test_{os.getpid()}.cpp"
    with open(tmpfile, "w") as f:
        f.write(code)

    # Call judge directly via docker exec
    # Actually, let's use the oj-backend API
    # For now, use a simpler approach: curl to the internal API
    try:
        # First get the problem's test case info
        r = subprocess.run(
            ["sudo", "docker", "exec", "oj-backend", "python3", "manage.py",
             "judge_submission", "--problem-id", problem_id,
             "--code-file", tmpfile, "--language", language],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120
        )
        output = r.stdout.decode() if isinstance(r.stdout, bytes) else r.stdout

        # Parse the judge output
        if "Accepted" in output or "AC" in output:
            return {"verdict": "AC", "details": output[:500]}
        elif "Wrong Answer" in output or "WA" in output:
            return {"verdict": "WA", "details": output[:500]}
        elif "Time Limit" in output or "TLE" in output:
            return {"verdict": "TLE", "details": output[:500]}
        else:
            return {"verdict": "UNKNOWN", "details": output[:500]}
    except Exception as e:
        return {"verdict": "ERROR", "details": str(e)[:500]}
    finally:
        if os.path.exists(tmpfile):
            os.remove(tmpfile)


def verify_pending_reports() -> int:
    """Verify all unverified reports. Returns count of verified AC fixes."""
    verified = 0
    with db_session() as db:
        reports = db.execute(
            """SELECT id, username, problem_id, submission_id, code_analysis
               FROM review_reports
               WHERE status = 'done' AND ac_after = 0
               ORDER BY created_at"""
        ).fetchall()

        for r in reports:
            report = dict(r)
            fix_code = extract_fix_code(report["code_analysis"] or "")

            if not fix_code:
                logger.info(f"{report['username']}/{report['problem_id']}: no fix code to verify")
                db.execute(
                    "UPDATE review_reports SET status = 'unverified' WHERE id = ?",
                    (report["id"],)
                )
                continue

            logger.info(f"Verifying {report['username']}/{report['problem_id']}...")
            result = submit_to_judge(report["problem_id"], fix_code)

            if result["verdict"] == "AC":
                verified += 1
                db.execute(
                    """UPDATE review_reports
                       SET status = 'verified', ac_after = 1
                       WHERE id = ?""",
                    (report["id"],)
                )
                logger.info(f"✅ {report['username']}/{report['problem_id']}: AC!")
            else:
                db.execute(
                    """UPDATE review_reports
                       SET status = CASE WHEN status = 'done' THEN 'unverified' ELSE status END
                       WHERE id = ?""",
                    (report["id"],)
                )
                logger.info(f"❌ {report['username']}/{report['problem_id']}: {result['verdict']}")

    return verified


def main():
    logger.info("Starting verification...")
    verified = verify_pending_reports()
    logger.info(f"Done: {verified} verified AC fixes")


if __name__ == "__main__":
    main()
