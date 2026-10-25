"""Run history (SQLite, data/agent/agent.db) and the event-listener checkpoint."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class RunStore:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, app_id TEXT, status TEXT, verdict TEXT,
                                             report_sha TEXT, created REAL, data TEXT);
            CREATE INDEX IF NOT EXISTS runs_app ON runs (app_id);
            CREATE INDEX IF NOT EXISTS runs_sha ON runs (report_sha);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            """)
        self.db.commit()

    def save(self, run: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO runs (id, app_id, status, verdict, report_sha, created, data) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET status=excluded.status, verdict=excluded.verdict,"
            " report_sha=excluded.report_sha, data=excluded.data",
            (run["runId"], run["appId"], run["status"], (run.get("verdict") or {}).get("status"),
             (run.get("report") or {}).get("sha256"), run.get("createdTs", time.time()), json.dumps(run)))
        self.db.commit()

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def by_report(self, sha: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT data FROM runs WHERE report_sha=?", (sha.lower(),)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit: int = 50, app_id: str | None = None) -> list[dict[str, Any]]:
        if app_id:
            rows = self.db.execute("SELECT data FROM runs WHERE app_id=? ORDER BY created DESC LIMIT ?",
                                   (app_id, limit)).fetchall()
        else:
            rows = self.db.execute("SELECT data FROM runs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def unfinished(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT data FROM runs WHERE status IN ('queued','running') ORDER BY created").fetchall()
        return [json.loads(r[0]) for r in rows]

    def counters(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT status, verdict, COUNT(*) FROM runs GROUP BY status, verdict").fetchall()
        out: dict[str, Any] = {"runs": 0, "clear": 0, "attention": 0, "flagged": 0, "failed": 0, "skipped": 0, "quarantined": 0}
        for status, verdict, n in rows:
            out["runs"] += n
            if status == "failed":
                out["failed"] += n
            elif status == "skipped":
                out["skipped"] += n
            elif status == "quarantined":
                out["quarantined"] = out.get("quarantined", 0) + n
            elif verdict in out:
                out[verdict] += n
        durs = [json.loads(r[0]).get("durationMs") for r in
                self.db.execute("SELECT data FROM runs WHERE status='anchored' ORDER BY created DESC LIMIT 50")]
        durs = [d for d in durs if d]
        out["avgDurationMs"] = int(sum(durs) / len(durs)) if durs else None
        return out

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
        self.db.commit()
