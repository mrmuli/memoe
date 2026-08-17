"""Persistent reflection job orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect
from memoe.services.memory_embeddings import refresh_memory_embeddings
from memoe.services.reflection_runner import run_reflection


@dataclass(frozen=True)
class ReflectionJob:
    """A persisted reflection job."""

    id: str
    status: str
    stage: str
    request_payload: dict[str, Any]
    reflection_run_id: str | None
    reflection_id: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


def create_reflection_job(
    provider: str,
    limit: int,
    goal: str | None,
    service_scope: str | None,
    settings: Settings | None = None,
) -> ReflectionJob:
    """Create a queued reflection job."""
    request_payload = {
        "provider": provider,
        "limit": limit,
        "goal": goal,
        "service_scope": service_scope,
    }
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            INSERT INTO reflection_jobs (status, stage, request_payload)
            VALUES ('queued', 'queued', %s)
            RETURNING *
            """,
            (Jsonb(request_payload),),
        )
        row = cursor.fetchone()
    return reflection_job_from_row(row)


def get_reflection_job(job_id: str, settings: Settings | None = None) -> ReflectionJob:
    """Load one reflection job."""
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT * FROM reflection_jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
    if not row:
        raise ValueError(f"Reflection job not found: {job_id}")
    return reflection_job_from_row(row)


def latest_reflection_job(settings: Settings | None = None) -> ReflectionJob | None:
    """Return the latest reflection job."""
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT *
            FROM reflection_jobs
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
    return reflection_job_from_row(row) if row else None


def run_reflection_job(job_id: str, settings: Settings | None = None) -> None:
    """Run a reflection job and persist status transitions."""
    resolved_settings = settings or Settings()
    try:
        update_reflection_job(job_id, status="running", stage="reflecting", settings=resolved_settings)
        job = get_reflection_job(job_id, resolved_settings)
        request = job.request_payload
        result = run_reflection(
            provider_name=str(request["provider"]),
            limit=int(request["limit"]),
            goal=request.get("goal"),
            service_scope=request.get("service_scope"),
            settings=resolved_settings,
        )
        update_reflection_job(
            job_id,
            status="embedding",
            stage="refreshing_embeddings",
            reflection_run_id=result.run_id,
            reflection_id=result.reflection_id,
            settings=resolved_settings,
        )
        refresh_memory_embeddings(resolved_settings)
        update_reflection_job(
            job_id,
            status="completed",
            stage="completed",
            reflection_run_id=result.run_id,
            reflection_id=result.reflection_id,
            completed=True,
            settings=resolved_settings,
        )
    except Exception as error:  # noqa: BLE001 - background jobs must persist failure state.
        update_reflection_job(
            job_id,
            status="failed",
            stage="failed",
            error_message=str(error),
            completed=True,
            settings=resolved_settings,
        )


def update_reflection_job(
    job_id: str,
    status: str,
    stage: str,
    reflection_run_id: str | None = None,
    reflection_id: str | None = None,
    error_message: str | None = None,
    completed: bool = False,
    settings: Settings | None = None,
) -> None:
    """Update reflection job status."""
    resolved_settings = settings or Settings()
    completed_sql = "now()" if completed else "completed_at"
    with connect(resolved_settings) as connection:
        connection.execute(
            f"""
            UPDATE reflection_jobs
            SET
              status = %s,
              stage = %s,
              reflection_run_id = COALESCE(%s, reflection_run_id),
              reflection_id = COALESCE(%s, reflection_id),
              error_message = %s,
              updated_at = now(),
              completed_at = {completed_sql}
            WHERE id = %s
            """,
            (status, stage, reflection_run_id, reflection_id, error_message, job_id),
        )


def reflection_job_from_row(row: dict[str, Any]) -> ReflectionJob:
    """Convert a database row into a reflection job."""
    return ReflectionJob(
        id=str(row["id"]),
        status=str(row["status"]),
        stage=str(row["stage"]),
        request_payload=dict(row["request_payload"]),
        reflection_run_id=str(row["reflection_run_id"]) if row.get("reflection_run_id") else None,
        reflection_id=str(row["reflection_id"]) if row.get("reflection_id") else None,
        error_message=str(row["error_message"]) if row.get("error_message") else None,
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
        completed_at=row["completed_at"].isoformat() if row.get("completed_at") else None,
    )
