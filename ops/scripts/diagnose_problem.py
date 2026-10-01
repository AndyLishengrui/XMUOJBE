#!/usr/bin/env python3
"""
Problem anomaly diagnosis — triggered by health_check when a problem has
unusually low AC rate (<5%). Investigates test cases for corruption.
"""
import os, sys, json, subprocess, argparse
from datetime import datetime

parser = argparse.ArgumentParser(description="Diagnose suspicious problem")
parser.add_argument("problem_id", help="Problem _id (e.g., JD127)")
parser.add_argument("--fix", action="store_true", help="Auto-fix test case corruption")
parser.add_argument("--verbose", "-v", action="store_true")
args = parser.parse_args()

PROBLEM_ID = args.problem_id
TIMESTAMP = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg):
    print(f"[{TIMESTAMP}] {msg}")

def run_pg(sql):
    """Run PostgreSQL query."""
    cmd = [
        "sudo", "docker", "exec", "oj-postgres",
        "psql", "-U", "onlinejudge", "-d", "onlinejudge",
        "-t", "-A", "-F", "\t", "-c", sql
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        log(f"PG error: {r.stderr[:200]}")
        return None
    return r.stdout.strip()


# ── 1. Problem info ─────────────────────────────────────────────────────

log(f"=== Diagnosing {PROBLEM_ID} ===")

sql = f"SELECT id, _id, title, contest_id, test_case_id, total_score FROM problem WHERE _id='{PROBLEM_ID}' AND visible=true"
row = run_pg(sql)
if not row:
    log(f"ERROR: Problem {PROBLEM_ID} not found")
    sys.exit(1)

fields = row.split("\t")
tc_id = fields[4]
title = fields[2]

# ── 2. Submission stats ──────────────────────────────────────────────────

sql = f"""
SELECT result, COUNT(*) FROM submission s
JOIN problem p ON s.problem_id = p.id
WHERE p._id = '{PROBLEM_ID}' AND s.create_time > NOW() - INTERVAL '30 days'
GROUP BY result ORDER BY COUNT(*) DESC
"""
stats = run_pg(sql)
log(f"Submission stats for '{title}':")
if stats:
    for line in stats.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2:
            result_map = {"0": "AC", "-1": "WA", "8": "Partial", "5": "RE", "4": "TLE", "1": "CE"}
            rname = result_map.get(parts[0], f"R{parts[0]}")
            log(f"  {rname}: {parts[1]}")

# ── 3. Test case verification ────────────────────────────────────────────

tc_dir = f"/OnlineJudgeDeploy/data/backend/test_case/{tc_id}"
if not os.path.exists(tc_dir):
    log(f"⚠️  Test case directory missing: {tc_dir}")
    sys.exit(0)

# Check for empty outputs (corruption indicator)
empty_outs = []
info_file = os.path.join(tc_dir, "info")
info_valid = True

if os.path.exists(info_file):
    try:
        with open(info_file) as f:
            info = json.load(f)
        cases = info.get("test_cases", {})
        for name, case in cases.items():
            out_file = os.path.join(tc_dir, f"{name}.out")
            if os.path.exists(out_file):
                size = os.path.getsize(out_file)
                if size == 0:
                    empty_outs.append(name)
                if args.verbose:
                    log(f"  TC{name}: {size}B output")
            else:
                log(f"  ⚠️  TC{name}: .out file MISSING")
    except json.JSONDecodeError:
        log("⚠️  info file corrupted (invalid JSON)")
        info_valid = False
else:
    log("⚠️  info file missing")
    info_valid = False

if empty_outs:
    log(f"🚨 EMPTY OUTPUT FILES: {empty_outs}")
    if args.fix:
        log("🔧 To fix empty outputs, restore from per-problem backup")
        # Check for backup .zip
        backup_zip = f"/OnlineJudgeDeploy/data/backend/test_case/{PROBLEM_ID}.zip"
        if os.path.exists(backup_zip):
            log(f"  Found backup: {backup_zip}")
            log("  Run: sudo unzip -o {backup_zip} -d {tc_dir}/")
            log("  Then: regenerate info file")

# ── 4. Check info file consistency ───────────────────────────────────────

if info_valid:
    actual_files = set(f for f in os.listdir(tc_dir) if f.endswith('.in') or f.endswith('.out'))
    expected_pairs = set()
    for name in cases:
        expected_pairs.add(f"{name}.in")
        expected_pairs.add(f"{name}.out")

    missing = expected_pairs - actual_files
    extra = actual_files - expected_pairs
    if missing:
        log(f"⚠️  Files in info but missing on disk: {missing}")
    if extra:
        log(f"⚠️  Files on disk but missing in info: {extra}")

# ── 5. Compare with original source if available ─────────────────────────

# Check AcWing problem mapping
log(f"\nDiagnosis complete for {PROBLEM_ID} '{title}'")
log(f"Test case dir: {tc_dir}")
log(f"Info valid: {info_valid}, Empty outputs: {len(empty_outs)}")
log("To auto-fix: python3 /home/ubuntu/xmuoj/scripts/diagnose_problem.py {PROBLEM_ID} --fix")
