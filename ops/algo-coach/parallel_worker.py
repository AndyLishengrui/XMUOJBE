#!/usr/bin/env python3
"""
Parallel Coach Worker Launcher — auto-scales worker count based on queue depth.

Queue depth → worker mapping:
  pending ≤ 5   → 1 worker  (light load)
  pending 6–20  → 2 workers (moderate load)
  pending > 20  → 3 workers (heavy load)

Usage:
  python3 parallel_worker.py           # auto-scale based on queue depth
  python3 parallel_worker.py --night   # relax CPU check + allow up to 3
  python3 parallel_worker.py --day     # strict CPU check + max 1 worker
"""

import argparse
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DIR = Path("/opt/algo-coach")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s"
)
logger = logging.getLogger("parallel-worker")


# 低峰窗口（北京时间）：只有这段时间才起 worker，窗口外一个都不留。
# 默认 00:00-08:00 —— 实测 OJ 流量最低的时段，只占全天请求的 1.2~1.6%；
# 而 16:00-21:00 的高峰占 11~20%。老师要求绝不和 OJ 抢资源。
COACH_WINDOW_START = int(os.environ.get("COACH_WINDOW_START", "0"))
COACH_WINDOW_END = int(os.environ.get("COACH_WINDOW_END", "8"))


def in_coach_window() -> bool:
    """当前是否在允许跑算法教练的低峰窗口内。支持跨零点（如 23-7）。"""
    hour = datetime.now().hour
    if COACH_WINDOW_START < COACH_WINDOW_END:
        return COACH_WINDOW_START <= hour < COACH_WINDOW_END
    return hour >= COACH_WINDOW_START or hour < COACH_WINDOW_END


def is_nighttime() -> bool:
    """窗口内就是低峰，等价于原来的 night 语义：放宽 CPU 检查
    （Ollama 自身的推理也会计入 load，用 load 当闸门会把自己卡死）。"""
    return in_coach_window()


def get_queue_depth() -> int:
    """Return pending (unlocked) + processing (locked) items in the review queue."""
    try:
        db = sqlite3.connect(str(DIR / "reports.db"))
        c = db.cursor()
        c.execute("SELECT COUNT(*) FROM review_queue")
        total = c.fetchone()[0]
        db.close()
        return total
    except Exception:
        return 0


def workers_for_queue(depth: int, max_workers: int = 3) -> int:
    """Dynamic worker count based on queue depth."""
    if depth <= 5:
        return 1
    elif depth <= 20:
        return min(2, max_workers)
    else:
        return min(3, max_workers)


class ParallelWorkerLauncher:
    def __init__(self, max_workers: int = 3, relax_cpu: bool = None,
                 force_night: bool = False, force_day: bool = False):
        self.max_workers = max_workers
        self.relax_cpu = relax_cpu if relax_cpu is not None else is_nighttime()
        self.force_night = force_night
        self.force_day = force_day
        self.window_paused = False
        self.processes: list[subprocess.Popen] = []
        self.running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        self._stop_all()

    def _stop_all(self):
        for i, p in enumerate(self.processes):
            if p.poll() is None:
                logger.info(f"Stopping worker-{i+1} (PID {p.pid})...")
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()

    def _start_one(self, worker_id: int) -> subprocess.Popen:
        env = os.environ.copy()
        if self.relax_cpu:
            env["COACH_RELAX_CPU"] = "1"
        env["COACH_WORKER_ID"] = str(worker_id)

        log_file = open(str(DIR / f"worker-{worker_id}.log"), "a")
        p = subprocess.Popen(
            [sys.executable, str(DIR / "worker.py"), "--child"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        logger.info(f"Started worker-{worker_id} (PID {p.pid}) → worker-{worker_id}.log")
        return p

    def run(self):
        # Determine initial worker count from queue depth
        queue_depth = get_queue_depth()
        target = workers_for_queue(queue_depth, self.max_workers) if in_coach_window() else 0
        mode = "night" if self.relax_cpu else "day"

        logger.info(f"=== Parallel Worker Launcher ===")
        logger.info(f"Queue: {queue_depth} | Workers: {target} | Max: {self.max_workers} | "
                     f"Mode: {mode} | RelaxCPU: {self.relax_cpu}")

        # Start initial workers
        self.num_workers = target
        for i in range(self.num_workers):
            p = self._start_one(i + 1)
            self.processes.append(p)
            time.sleep(2)

        logger.info(f"All {self.num_workers} workers running. Auto-scaling every 30s...")

        # Monitor loop
        ticks_since_last_check = 0
        while self.running:
            for i, p in enumerate(self.processes):
                if p.poll() is not None:
                    exit_code = p.returncode
                    logger.warning(f"Worker-{i+1} (PID {p.pid}) exited with code {exit_code}")
                    if self.running:
                        logger.info(f"Restarting worker-{i+1}...")
                        time.sleep(5)
                        self.processes[i] = self._start_one(i + 1)

            # Every 30s: check queue depth and auto-scale
            ticks_since_last_check += 1
            if ticks_since_last_check >= 15:  # 15 × 2s sleep = 30s
                ticks_since_last_check = 0

                # Update queue depth
                queue_depth = get_queue_depth()
                if in_coach_window():
                    target = workers_for_queue(queue_depth, self.max_workers)
                    if self.window_paused:
                        logger.info("进入低峰窗口，恢复处理队列")
                        self.window_paused = False
                else:
                    # 低峰窗口外一个 worker 都不留：绝不和 OJ 抢资源
                    target = 0
                    if not self.window_paused:
                        logger.info(
                            f"低峰窗口外（北京时间 {datetime.now().hour}:00，窗口 "
                            f"{COACH_WINDOW_START}:00-{COACH_WINDOW_END}:00）—— 停止全部 worker")
                        self.window_paused = True

                # Only scale if target differs
                live = len([p for p in self.processes if p.poll() is None])
                if target != live:
                    logger.info(f"Queue {queue_depth} → scaling: {live} → {target} workers")
                    self._scale(target)

                # Day/night transition (only if not forced)
                if not self.force_night and not self.force_day:
                    now_night = is_nighttime()
                    if now_night != self.relax_cpu:
                        logger.info(f"Day/night transition: relax_cpu {self.relax_cpu} → {now_night}")
                        self.relax_cpu = now_night
                        # Update max_workers: night can use 3, day capped at 2
                        self.max_workers = 3 if now_night else 2
                        # Re-evaluate target with new max
                        target = workers_for_queue(queue_depth, self.max_workers)
                        live = len([p for p in self.processes if p.poll() is None])
                        if target != live:
                            self._scale(target)

            time.sleep(2)

        logger.info("Launcher stopped.")

    def _scale(self, target: int):
        """Scale worker count up or down."""
        current = len([p for p in self.processes if p.poll() is None])
        if target > current:
            # Scale up
            for i in range(current, target):
                worker_id = i + 1
                # Reuse or append slot
                if worker_id <= len(self.processes):
                    self.processes[worker_id - 1] = self._start_one(worker_id)
                else:
                    p = self._start_one(worker_id)
                    self.processes.append(p)
                time.sleep(2)
        elif target < current:
            # Scale down
            for i in range(current - 1, target - 1, -1):
                if i < len(self.processes):
                    p = self.processes[i]
                    if p.poll() is None:
                        logger.info(f"Scaling down: stopping worker-{i+1} (PID {p.pid})")
                        p.terminate()
                        try:
                            p.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            p.kill()
                            p.wait()
                    self.processes.pop(i)
        self.num_workers = target


def main():
    parser = argparse.ArgumentParser(description="Parallel Coach Worker Launcher (auto-scale)")
    parser.add_argument("--workers", "-w", type=int, default=None,
                        help="Force exact worker count (overrides auto-scale)")
    parser.add_argument("--night", action="store_true",
                        help="Force night mode (relax CPU, up to 3 workers)")
    parser.add_argument("--day", action="store_true",
                        help="Force day mode (strict CPU, max 2 workers)")
    parser.add_argument("--relax-cpu", action="store_true", default=None,
                        help="Relax CPU load check")
    args = parser.parse_args()

    # Determine mode
    force_night = args.night
    force_day = args.day
    relax_cpu = args.relax_cpu
    if force_night:
        relax_cpu = True
    elif force_day:
        relax_cpu = False

    max_workers = 3 if force_night else (1 if force_day else (3 if is_nighttime() else 2))

    launcher = ParallelWorkerLauncher(
        max_workers=max_workers,
        relax_cpu=relax_cpu,
        force_night=force_night,
        force_day=force_day,
    )
    launcher.run()


if __name__ == "__main__":
    main()
