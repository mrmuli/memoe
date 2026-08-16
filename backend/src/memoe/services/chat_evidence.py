"""Evidence expansion for Memoe chat answers."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from memoe.config import Settings
from memoe.db.connection import connect

EVIDENCE_KEYWORDS = (
    "evidence",
    "ticket",
    "jira",
    "issue",
    "incident number",
    "id",
    "identifier",
    "timestamp",
    "when",
    "details",
    "show me",
    "share",
)


def should_expand_evidence(message: str) -> bool:
    """Return true when a user asks for raw supporting evidence."""
    normalized = message.lower()
    return any(keyword in normalized for keyword in EVIDENCE_KEYWORDS)


def expand_evidence_for_memory(
    retrieved_memory: list[dict[str, Any]],
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
              oe.role
            FROM observation_evidence oe
            JOIN events e ON e.id = oe.event_id
            LEFT JOIN services s ON s.id = e.service_id
            WHERE oe.observation_id IN ({observation_placeholders})
              AND oe.role = 'supporting'
            ORDER BY e.occurred_at ASC, e.source_table ASC
            LIMIT %s
            """,
            [*observation_id_values, limit],
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
        "evidence_role": row["role"],
    }
