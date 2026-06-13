import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.expanduser("~/.agent-ref-pipeline/queue.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_url TEXT NOT NULL,
            video_id TEXT NOT NULL,
            video_title TEXT,
            status TEXT DEFAULT 'pending',
            error_message TEXT,
            discord_message_id TEXT,
            discord_user TEXT,
            retry_count INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            processed_at DATETIME,
            UNIQUE(video_id)
        );
        CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
        CREATE INDEX IF NOT EXISTS idx_queue_created ON queue(created_at);
    """)
    conn.commit()
    conn.close()


def enqueue(url, video_id, message_id, user):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO queue (youtube_url, video_id, discord_message_id, discord_user) VALUES (?, ?, ?, ?)",
            (url, video_id, message_id, user),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def is_duplicate(video_id):
    conn = _get_conn()
    row = conn.execute("SELECT 1 FROM queue WHERE video_id = ?", (video_id,)).fetchone()
    conn.close()
    return row is not None


def get_pending(limit=5):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM queue WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_processing(item_id):
    conn = _get_conn()
    conn.execute("UPDATE queue SET status = 'processing' WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()


def set_completed(item_id):
    conn = _get_conn()
    conn.execute(
        "UPDATE queue SET status = 'completed', processed_at = ? WHERE id = ?",
        (datetime.now().isoformat(), item_id),
    )
    conn.commit()
    conn.close()


def set_failed(item_id, error, max_retries=3):
    conn = _get_conn()
    conn.execute(
        "UPDATE queue SET retry_count = retry_count + 1, error_message = ? WHERE id = ?",
        (error, item_id),
    )
    conn.commit()
    row = conn.execute("SELECT retry_count FROM queue WHERE id = ?", (item_id,)).fetchone()
    if row and row["retry_count"] >= max_retries:
        conn.execute("UPDATE queue SET status = 'failed' WHERE id = ?", (item_id,))
    else:
        conn.execute("UPDATE queue SET status = 'pending' WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
