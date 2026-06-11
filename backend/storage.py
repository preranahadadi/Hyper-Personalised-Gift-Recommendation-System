from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DB_PATH = Path(__file__).parent / "workflow_history.sqlite3"
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                thread_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS steps (
                thread_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS states (
                thread_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def save_run(thread_id: str, run: Dict[str, Any]) -> None:
    payload = json.dumps(run, ensure_ascii=False, default=str)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (thread_id, data, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (thread_id, payload, _now_iso()),
        )


def save_steps(thread_id: str, steps: List[Dict[str, Any]]) -> None:
    payload = json.dumps(steps, ensure_ascii=False, default=str)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO steps (thread_id, data, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (thread_id, payload, _now_iso()),
        )


def save_state(thread_id: str, state: Dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, default=str)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO states (thread_id, data, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (thread_id, payload, _now_iso()),
        )


def load_runs() -> Dict[str, Dict[str, Any]]:
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT thread_id, data FROM runs").fetchall()
    return {thread_id: json.loads(data) for thread_id, data in rows}


def load_steps(thread_id: str) -> List[Dict[str, Any]]:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT data FROM steps WHERE thread_id = ?", (thread_id,)).fetchone()
    return json.loads(row[0]) if row else []


def load_all_steps() -> Dict[str, List[Dict[str, Any]]]:
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT thread_id, data FROM steps").fetchall()
    return {thread_id: json.loads(data) for thread_id, data in rows}


def load_state(thread_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT data FROM states WHERE thread_id = ?", (thread_id,)).fetchone()
    return json.loads(row[0]) if row else None
