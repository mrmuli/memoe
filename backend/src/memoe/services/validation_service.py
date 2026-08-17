"""Record validation results against Memoe memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect

VALIDATION_RESULT_TYPES = {"validated", "weakened", "superseded", "needs_recheck", "inconclusive"}


@dataclass(frozen=True)
class ValidationResult:
    """Created validation result summary."""

    run_id: str
    result_id: str
    target_type: str
    target_id: str
    result_type: str


@dataclass(frozen=True)
class ValidationSummary:
    """Validation result for CLI display."""

    id: str
    created_at: str
    source: str
    result_type: str
    target_type: str
    target_id: str
    summary: str


def add_validation_result(
    source: str,
    result_type: str,
    summary: str,
    observation_id: str | None = None,
    reflection_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> ValidationResult:
    """Add one validation result and update target lifecycle when applicable."""
    if result_type not in VALIDATION_RESULT_TYPES:
        raise ValueError(f"Unsupported validation result type: {result_type}")
    if bool(observation_id) == bool(reflection_id):
        raise ValueError("Provide exactly one of observation_id or reflection_id.")
    if not summary:
        raise ValueError("Validation summary is required.")

    target_type = "observation" if observation_id else "reflection"
    target_id = str(observation_id or reflection_id)
    payload = {
        "target_type": target_type,
        "target_id": target_id,
        "summary": summary,
    }

    with connect(settings) as connection:
        run_id = create_validation_run(connection, source, payload)
        result_id = insert_validation_result(
            connection=connection,
            run_id=run_id,
            source=source,
            result_type=result_type,
            summary=summary,
            observation_id=observation_id,
            reflection_id=reflection_id,
            evidence=evidence or {},
        )
        if observation_id:
            update_observation_lifecycle(connection, observation_id, result_type)
        complete_validation_run(connection, run_id, {"result_id": result_id})

    return ValidationResult(
        run_id=run_id,
        result_id=result_id,
        target_type=target_type,
        target_id=target_id,
        result_type=result_type,
    )


def create_validation_run(connection, source: str, query: dict[str, Any]) -> str:
    """Create a completed manual validation run shell."""
    row = connection.execute(
        """
        INSERT INTO validation_runs (source, status, query)
        VALUES (%s, 'running', %s)
        RETURNING id
        """,
        (source, Jsonb(query)),
    ).fetchone()
    return str(row[0])


def insert_validation_result(
    connection,
    run_id: str,
    source: str,
    result_type: str,
    summary: str,
    observation_id: str | None,
    reflection_id: str | None,
    evidence: dict[str, Any],
) -> str:
    """Insert the validation result row."""
    row = connection.execute(
        """
        INSERT INTO validation_results (
          validation_run_id,
          observation_id,
          reflection_id,
          source,
          result_type,
          summary,
          evidence
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            run_id,
            observation_id,
            reflection_id,
            source,
            result_type,
            summary,
            Jsonb(evidence),
        ),
    ).fetchone()
    return str(row[0])


def update_observation_lifecycle(connection, observation_id: str, result_type: str) -> None:
    """Update observation lifecycle from a validation result."""
    lifecycle_status = "needs_recheck" if result_type == "inconclusive" else result_type
    connection.execute(
        """
        UPDATE observations
        SET
          lifecycle_status = %s,
          last_checked_at = now(),
          valid_until = CASE
            WHEN %s IN ('weakened', 'superseded', 'needs_recheck') THEN now()
            ELSE valid_until
          END
        WHERE id = %s
        """,
        (lifecycle_status, lifecycle_status, observation_id),
    )


def complete_validation_run(connection, run_id: str, raw_response: dict[str, Any]) -> None:
    """Mark a validation run completed."""
    connection.execute(
        """
        UPDATE validation_runs
        SET status = 'completed', raw_response = %s, completed_at = now()
        WHERE id = %s
        """,
        (Jsonb(raw_response), run_id),
    )


def list_validation_results(
    observation_id: str | None = None,
    reflection_id: str | None = None,
    limit: int = 10,
    settings: Settings | None = None,
) -> list[ValidationSummary]:
    """List validation results."""
    query = """
        SELECT
          id,
          created_at,
          source,
          result_type,
          observation_id,
          reflection_id,
          summary
        FROM validation_results
    """
    params: list[Any] = []
    if observation_id:
        query += " WHERE observation_id = %s"
        params.append(observation_id)
    elif reflection_id:
        query += " WHERE reflection_id = %s"
        params.append(reflection_id)

    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    with connect(settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    return [
        ValidationSummary(
            id=str(row["id"]),
            created_at=row["created_at"].isoformat(),
            source=str(row["source"]),
            result_type=str(row["result_type"]),
            target_type="observation" if row["observation_id"] else "reflection",
            target_id=str(row["observation_id"] or row["reflection_id"]),
            summary=str(row["summary"]),
        )
        for row in rows
    ]
