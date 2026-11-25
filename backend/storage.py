import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(os.getenv("XENO_DB_PATH", "data/xeno.sqlite3")).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _db():
    # sqlite3's own context manager only commits/rolls back; it does not close the
    # connection. Close explicitly so file handles do not accumulate under load.
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            progress REAL,
            downloaded_bytes INTEGER DEFAULT 0,
            filename TEXT,
            size_bytes INTEGER,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")


def create_job(job_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute("INSERT INTO jobs(id,status,progress,created_at,updated_at) VALUES(?,?,?,?,?)",
                  (job_id, "queued", 0, now, now))


def update_job(job_id: str, **fields):
    allowed = {"status", "progress", "downloaded_bytes", "filename", "size_bytes", "error"}
    values = {k: v for k, v in fields.items() if k in allowed}
    if not values:
        return
    values["updated_at"] = datetime.now(timezone.utc).isoformat()
    sql = ", ".join(f"{k}=?" for k in values)
    with _db() as c:
        c.execute(f"UPDATE jobs SET {sql} WHERE id=?", (*values.values(), job_id))


def get_job(job_id: str):
    with _db() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None
