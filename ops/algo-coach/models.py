"""Database models for the WA Review Report system. Uses SQLite to avoid touching XMUOJ's PostgreSQL."""

import sqlite3
import os
import time
from contextlib import contextmanager

DB_PATH = "/opt/algo-coach/reports.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS review_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            problem_id TEXT NOT NULL,      -- e.g. 'LinK24'
            problem_title TEXT NOT NULL,
            submission_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | done | failed
            code_analysis TEXT,            -- AI analysis of the code
            hints TEXT,                    -- guided hints for student
            common_pitfall TEXT,           -- common mistake category
            created_at REAL NOT NULL,
            processed_at REAL,
            ac_after BOOLEAN DEFAULT 0,   -- did student AC after seeing report?
            view_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            problem_id TEXT NOT NULL,
            problem_title TEXT NOT NULL,
            submission_id TEXT NOT NULL UNIQUE,
            wa_count INTEGER NOT NULL,
            code_snippet TEXT NOT NULL,
            priority INTEGER DEFAULT 0,  -- higher = more important
            queued_at REAL NOT NULL,
            locked_by TEXT,
            locked_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_reports_user
            ON review_reports(username, problem_id);
        CREATE INDEX IF NOT EXISTS idx_reports_submission
            ON review_reports(submission_id);
        CREATE INDEX IF NOT EXISTS idx_queue_priority
            ON review_queue(priority DESC, queued_at);
    """)
    db.commit()
    db.close()


@contextmanager
def db_session():
    db = get_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Initialize on import
if not os.path.exists(DB_PATH):
    init_db()
