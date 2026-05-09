"""
Agent Trace Store — SQLite-backed behavior log with 30-day retention.

Stores every agent action (task processing, tool execution, LLM calls,
pipeline events) as structured rows for query and audit.

Design:
  - Single SQLite file: /var/lib/agentforge/traces.db
  - 30-day auto-cleanup on startup + hourly
  - ~10KB/day total storage (8 agents × ~100 actions/day)
  - Thread-safe via WAL mode + connection per thread
"""

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agentforge.traces")

DB_PATH = Path("/var/lib/agentforge/traces.db")
RETENTION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,          -- ISO 8601 timestamp
    agent_id  TEXT    NOT NULL,          -- zhugeliang, guanyu, ...
    event     TEXT    NOT NULL,          -- task_start, tool_exec, llm_call, pipeline, feishu_reply, subagent_spawn, error
    task_id   TEXT,                      -- Redis msg_id or boot-xxx
    message   TEXT,                      -- user message (truncated)
    tool      TEXT,                      -- tool name (zentao_bug_query, ...)
    model     TEXT,                      -- LLM model used
    duration_ms INTEGER,                 -- elapsed milliseconds
    status    TEXT,                      -- ok, error, raw, pending
    detail    TEXT,                      -- JSON blob with extra fields
    created   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_traces_agent_ts ON traces(agent_id, ts);
CREATE INDEX IF NOT EXISTS idx_traces_event     ON traces(event);
CREATE INDEX IF NOT EXISTS idx_traces_task      ON traces(task_id);
CREATE INDEX IF NOT EXISTS idx_traces_created   ON traces(created);

-- Enable WAL for concurrent reads
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
"""


class TraceStore:
    """Thread-safe agent action logger."""

    def __init__(self, db_path: str = str(DB_PATH), retention_days: int = RETENTION_DAYS):
        self.db_path = db_path
        self.retention_days = retention_days
        self._local = threading.local()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._cleanup()

    def _get_conn(self) -> sqlite3.Connection:
        """Per-thread connection."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript(SCHEMA)
        conn.commit()

    def _cleanup(self):
        """Delete records older than retention_days."""
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).isoformat()
        try:
            conn = self._get_conn()
            cursor = conn.execute("DELETE FROM traces WHERE created < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.commit()
            if deleted > 0:
                logger.info("[traces] Cleaned up %d old records (before %s)", deleted, cutoff[:10])
        except Exception as e:
            logger.warning("[traces] Cleanup failed: %s", e)

    # =========================================================================
    #  Public API
    # =========================================================================

    def log(self, agent_id: str, event: str, **kwargs):
        """Log an agent event. All kwargs become columns or detail JSON."""
        ts = datetime.now().isoformat()
        row = {
            "ts": ts, "agent_id": agent_id, "event": event,
            "task_id": kwargs.pop("task_id", None),
            "message": (kwargs.pop("message", "") or "")[:200],
            "tool": kwargs.pop("tool", None),
            "model": kwargs.pop("model", None),
            "duration_ms": kwargs.pop("duration_ms", None),
            "status": kwargs.pop("status", None),
            "detail": json.dumps(kwargs, ensure_ascii=False) if kwargs else None,
        }
        try:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO traces (ts, agent_id, event, task_id, message, tool, model, duration_ms, status, detail)
                   VALUES (:ts, :agent_id, :event, :task_id, :message, :tool, :model, :duration_ms, :status, :detail)""",
                row,
            )
            conn.commit()
        except Exception as e:
            logger.error("[traces] Write failed: %s", e)

    # =========================================================================
    #  Query helpers
    # =========================================================================

    def query(self, agent_id: str = None, event: str = None,
              since: str = None, limit: int = 50) -> list[dict]:
        """Query traces with optional filters."""
        conn = self._get_conn()
        sql = "SELECT * FROM traces WHERE 1=1"
        params = []
        if agent_id:
            sql += " AND agent_id = ?"; params.append(agent_id)
        if event:
            sql += " AND event = ?"; params.append(event)
        if since:
            sql += " AND ts >= ?"; params.append(since)
        sql += " ORDER BY ts DESC LIMIT ?"; params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        cols = [d[1] for d in conn.execute("PRAGMA table_info(traces)")]  # col[1] = name
        return [dict(zip(cols, tuple(r))) for r in rows]

    def recent_errors(self, agent_id: str = None, limit: int = 20) -> list[dict]:
        return self.query(agent_id=agent_id, event="error", limit=limit)

    def agent_summary(self, since: str = None) -> list[dict]:
        """Per-agent event counts in time range."""
        conn = self._get_conn()
        params = []
        sql = "SELECT agent_id, event, COUNT(*) as cnt FROM traces WHERE 1=1"
        if since:
            sql += " AND ts >= ?"; params.append(since)
        sql += " GROUP BY agent_id, event ORDER BY agent_id, cnt DESC"
        return [dict(zip(["agent_id","event","cnt"], r)) for r in conn.execute(sql, params).fetchall()]

    def total_size(self) -> int:
        """Return approximate row count."""
        return self._get_conn().execute("SELECT COUNT(*) FROM traces").fetchone()[0]

    def periodic_cleanup(self, interval: int = 3600):
        """Call this every hour from main loop to purge old records."""
        # Use a simple timestamp check to avoid running every iteration
        if not hasattr(self, "_last_cleanup"):
            self._last_cleanup = 0
        now = time.time()
        if now - self._last_cleanup > interval:
            self._cleanup()
            self._last_cleanup = now


# =========================================================================
#  Singleton
# =========================================================================

traces = TraceStore()
