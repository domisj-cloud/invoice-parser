from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class JobRepository:
    path: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> "JobRepository":
        path = Path(settings.database_path or Path(settings.work_dir) / "jobs.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        repo = cls(path)
        repo.initialize()
        return repo

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    input_bucket TEXT NOT NULL,
                    input_object_key TEXT NOT NULL,
                    output_bucket TEXT,
                    output_object_key TEXT,
                    error_bucket TEXT,
                    error_object_key TEXT,
                    error_report_object_key TEXT,
                    invoice_number TEXT,
                    document_type TEXT,
                    line_count INTEGER,
                    error_type TEXT,
                    error_message TEXT,
                    error_log TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_ms INTEGER
                )
                """
            )

    def create(
        self,
        *,
        job_id: str,
        input_bucket: str,
        input_object_key: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, status, input_bucket, input_object_key, started_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, "PROCESSING", input_bucket, input_object_key, utc_now()),
            )

    def update(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )

    def complete(
        self,
        job_id: str,
        *,
        status: str,
        started_at: datetime,
        **values: Any,
    ) -> None:
        completed_at = datetime.now(UTC)
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)
        self.update(
            job_id,
            status=status,
            completed_at=completed_at.isoformat(timespec="milliseconds"),
            duration_ms=duration_ms,
            **values,
        )

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection
