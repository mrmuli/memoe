"""Evidence expansion for Memoe chat answers."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from memoe.config import Settings
from memoe.db.connection import connect


def expand_evidence_for_memory(
    retrieved_memory: list[dict[str, Any]],
    answer_mode: str | None = None,
    settings: Settings | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Expand retrieved observations/reflections into compact raw evidence events."""
    if not retrieved_memory:
        return []

    observation_ids = {
        row["memory_id"]
        for row in retrieved_memory
        if row.get("memory_type") == "observation" and row.get("memory_id")
    }
    reflection_ids = [
        row["memory_id"]
        for row in retrieved_memory
        if row.get("memory_type") == "reflection" and row.get("memory_id")
    ]

    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        if reflection_ids:
            reflection_placeholders = ", ".join(["%s"] * len(reflection_ids))
            cursor.execute(
                f"""
                SELECT DISTINCT observation_id
                FROM reflection_observations
                WHERE reflection_id IN ({reflection_placeholders})
                  AND role = 'supporting'
                """,
                reflection_ids,
            )
            observation_ids.update(str(row["observation_id"]) for row in cursor.fetchall())

        if not observation_ids:
            return []

        observation_id_values = list(observation_ids)
        observation_placeholders = ", ".join(["%s"] * len(observation_id_values))
        cursor.execute(
            f"""
            SELECT DISTINCT
              e.id,
              s.slug AS service_slug,
              e.source_table,
              e.category,
              e.event_type,
              e.occurred_at,
              e.component,
              e.severity,
              e.summary,
              e.external_reference,
              e.correlation_identifiers,
              e.metadata,
              oe.role,
              CASE
                WHEN %s = 'ticket_lookup' AND e.source_table = 'jira_issue' THEN 0
                ELSE 1
              END AS sort_rank
            FROM observation_evidence oe
            JOIN events e ON e.id = oe.event_id
            LEFT JOIN services s ON s.id = e.service_id
            WHERE oe.observation_id IN ({observation_placeholders})
              AND oe.role = 'supporting'
            ORDER BY
              sort_rank,
              e.occurred_at ASC,
              e.source_table ASC
            LIMIT %s
            """,
            [answer_mode, *observation_id_values, limit],
        )
        rows = cursor.fetchall()

    return [evidence_event_from_row(row) for row in rows]


def evidence_event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw event row into compact chat context."""
    return {
        "id": str(row["id"]),
        "service_slug": row["service_slug"],
        "source_table": row["source_table"],
        "category": row["category"],
        "event_type": row["event_type"],
        "occurred_at": row["occurred_at"].isoformat(),
        "component": row["component"],
        "severity": row["severity"],
        "summary": row["summary"],
        "external_reference": row["external_reference"],
        "correlation_identifiers": row["correlation_identifiers"],
        "source_details": source_details(row["source_table"], row.get("metadata")),
        "evidence_role": row["role"],
    }


def source_details(source_table: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return source-specific details useful for chat without sending full raw rows."""
    if not metadata:
        return {}

    if source_table == "jira_issue":
        return pick_fields(
            metadata,
            [
                "key",
                "project_key",
                "issue_type",
                "priority",
                "status",
                "status_category",
                "summary",
                "description",
                "labels",
                "creator_display_name",
                "assignee_display_name",
                "created",
                "updated",
            ],
        )

    if source_table == "aws_cloudwatch_alarm":
        return pick_fields(
            metadata,
            [
                "name",
                "state_value",
                "state_reason",
                "metric_name",
                "namespace",
                "threshold",
                "comparison_operator",
                "dimensions",
            ],
        )

    return {}


def pick_fields(payload: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Return a compact dict with non-empty fields only."""
    return {field: payload[field] for field in fields if payload.get(field) is not None}
