"""SQLite-backed instance registry.

Persists across restarts so the reaper can reconcile against `docker ps` and
never orphans a container. Thread-safe: the orchestrator runs blocking Docker
calls in worker threads, so all DB access is guarded by a single lock over one
WAL-mode connection.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

# Instance status lifecycle:
#   starting -> running -> (expired | killed | error)
ACTIVE_STATES = ("starting", "running")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    uuid            TEXT PRIMARY KEY,
    source_ip       TEXT NOT NULL,
    challenge       TEXT NOT NULL,
    status          TEXT NOT NULL,
    container_id    TEXT,
    container_name  TEXT,
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL,
    rpc_url         TEXT,
    player_address  TEXT,
    player_privkey  TEXT,
    deployer_address TEXT,
    package_id      TEXT,
    objects_json    TEXT,
    flag_mode       TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_status ON instances(status);
CREATE INDEX IF NOT EXISTS idx_ip ON instances(source_ip);
"""


class Registry:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # --- writes ------------------------------------------------------------
    def create(self, *, uuid: str, source_ip: str, challenge: str,
               container_name: str, expires_at: float, flag_mode: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO instances (uuid, source_ip, challenge, status, "
                "container_name, created_at, expires_at, flag_mode) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (uuid, source_ip, challenge, "starting", container_name,
                 time.time(), expires_at, flag_mode),
            )
            self._db.commit()

    def set_container(self, uuid: str, container_id: str) -> None:
        self._update(uuid, container_id=container_id)

    def set_running(self, uuid: str, *, rpc_url: str, player_address: str,
                    player_privkey: str, deployer_address: str,
                    package_id: str, objects: list[dict[str, Any]]) -> None:
        self._update(
            uuid,
            status="running",
            rpc_url=rpc_url,
            player_address=player_address,
            player_privkey=player_privkey,
            deployer_address=deployer_address,
            package_id=package_id,
            objects_json=json.dumps(objects),
        )

    def set_status(self, uuid: str, status: str, error: Optional[str] = None) -> None:
        self._update(uuid, status=status, error=error)

    def _update(self, uuid: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._db.execute(
                f"UPDATE instances SET {cols} WHERE uuid=?",
                (*fields.values(), uuid),
            )
            self._db.commit()

    # --- reads -------------------------------------------------------------
    def get(self, uuid: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM instances WHERE uuid=?", (uuid,)
            ).fetchone()
        return self._row_to_dict(row)

    def active_count_for_ip(self, source_ip: str) -> int:
        with self._lock:
            row = self._db.execute(
                f"SELECT COUNT(*) AS c FROM instances WHERE source_ip=? "
                f"AND status IN ({_qmarks(ACTIVE_STATES)})",
                (source_ip, *ACTIVE_STATES),
            ).fetchone()
        return int(row["c"])

    def active_for_ip(self, source_ip: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                f"SELECT * FROM instances WHERE source_ip=? "
                f"AND status IN ({_qmarks(ACTIVE_STATES)}) "
                f"ORDER BY created_at DESC LIMIT 1",
                (source_ip, *ACTIVE_STATES),
            ).fetchone()
        return self._row_to_dict(row)

    def total_active(self) -> int:
        with self._lock:
            row = self._db.execute(
                f"SELECT COUNT(*) AS c FROM instances "
                f"WHERE status IN ({_qmarks(ACTIVE_STATES)})",
                ACTIVE_STATES,
            ).fetchone()
        return int(row["c"])

    def all_active(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM instances WHERE status IN ({_qmarks(ACTIVE_STATES)})",
                ACTIVE_STATES,
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def due_for_reap(self, now: Optional[float] = None) -> list[dict[str, Any]]:
        now = now if now is not None else time.time()
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM instances WHERE status IN ({_qmarks(ACTIVE_STATES)}) "
                f"AND expires_at < ?",
                (*ACTIVE_STATES, now),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        d["objects"] = json.loads(d.get("objects_json") or "[]")
        return d


def _qmarks(seq) -> str:
    return ",".join("?" for _ in seq)
