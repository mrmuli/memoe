"""Load synthetic fixtures into CockroachDB."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect
from memoe.normalizers import NormalizedEvent, normalize_row

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures"
SOURCE_TABLE_FILES = {
    "aws_cloudwatch_alarm": "aws_cloudwatch_alarm.json",
    "aws_cloudwatch_log_event": "aws_cloudwatch_log_event.json",
    "github_repository_deployment": "github_repository_deployment.json",
    "github_pull_request": "github_pull_request.json",
    "jira_issue": "jira_issue.json",
}

OBSERVATION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "statement",
        "observation_type",
        "confidence",
        "evidence_quality",
        "supporting_evidence_ids",
        "rejected_evidence_ids",
        "limitations",
        "reasoning_summary",
    ],
    "properties": {
        "statement": {"type": "string"},
        "observation_type": {
            "type": "string",
            "enum": [
                "deployment_impact",
                "recurring_pattern",
                "recovery_pattern",
                "hotspot",
                "emerging_trend",
                "memoe_system",
                "inconclusive",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_quality": {
            "type": "object",
            "required": ["rating", "strengths", "gaps"],
            "properties": {
                "rating": {
                    "type": "string",
                    "enum": ["strong", "moderate", "limited", "insufficient"],
                },
                "strengths": {"type": "array", "items": {"type": "string"}},
                "gaps": {"type": "array", "items": {"type": "string"}},
            },
        },
        "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "rejected_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "reasoning_summary": {"type": "string"},
    },
}

OBSERVATION_PROCEDURE = """You are Memoe, an operational memory system.

Your job is to generate evidence-backed observations for SREs.

Apply these procedural skills when relevant:
- SLO analysis: inspect objective, threshold, burn-rate/window language, severity, and customer impact.
- OpenTelemetry evidence assessment: look for traces, metrics, logs, spans, errors, dependency signals, and missing telemetry.
- Postmortem analysis: separate timeline, impact, contributing factors, unknowns, and follow-up evidence.
- Service architecture reasoning: use component names and source context to judge whether evidence is on the affected path, but do not invent architecture details.

Procedure:
1. Separate facts from interpretation.
2. Consider operational signals, operational events, operational outcomes, and telemetry.
3. Identify which evidence supports a possible relationship.
4. Identify which evidence appears unrelated or insufficient.
5. Assess evidence quality before writing the observation.
   - Rate the evidence as strong, moderate, limited, or insufficient.
   - Identify evidence strengths, such as aligned signal/event/outcome timing, service-scoped source mapping, relevant component context, telemetry showing request/dependency errors, or customer-impact evidence.
   - Identify evidence gaps, such as missing telemetry, logs, traces, rollback/recovery data, historical recurrence, RCA links, or architecture documentation.
6. Use cautious causal language.
   - Prefer "temporally associated with", "consistent with", "may have contributed to", or "possible deployment impact".
   - Avoid "caused", "introduced", "resulted in", or "indicating a deployment impact" unless there is direct evidence such as rollback recovery, error traces, linked incident RCA, or repeated historical pattern.
7. Cite evidence IDs for every important claim.
8. State limitations and missing evidence.
9. Respect chronology.
   - Do not suggest deployment impact if degradation signals, error telemetry, or customer outcomes began before the deployment, unless there is explicit evidence that the deployment or rollout had already started earlier.
   - If a deployment happened after the degradation started, treat it as unrelated, recovery-context, or inconclusive unless later evidence clearly links it.
10. If evidence only shows timing relationships, describe the observation as a correlation or hypothesis, not a confirmed impact.
11. Return only valid JSON matching the output schema.
"""


@dataclass(frozen=True)
class SeedResult:
    """Summary of a seed load operation."""

    scenario: str
    services: int
    event_sources: int
    events: int
    procedures: int


@dataclass(frozen=True)
class EvidenceRow:
    """Evidence row used by CLI display."""

    id: str
    service_slug: str
    occurred_at: str
    category: str
    event_type: str
    component: str | None
    summary: str
    source_table: str


def load_fixture_rows(scenario: str) -> list[tuple[str, dict[str, Any]]]:
    """Read all supported fixture rows for a scenario."""
    scenario_path = FIXTURE_ROOT / scenario
    if not scenario_path.exists():
        raise ValueError(f"Unknown fixture scenario: {scenario}")

    rows: list[tuple[str, dict[str, Any]]] = []
    for source_table, file_name in SOURCE_TABLE_FILES.items():
        fixture_path = scenario_path / file_name
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise TypeError(f"Fixture must contain a JSON array: {fixture_path}")

        for row in data:
            if not isinstance(row, dict):
                raise TypeError(f"Fixture row must be a JSON object: {fixture_path}")
            rows.append((source_table, row))

    return rows


def service_name_from_slug(slug: str) -> str:
    """Convert a fixture service slug into a display name."""
    return slug.replace("-", " ").title()


def upsert_service(connection: Connection, service_slug: str) -> str:
    """Insert or update a service and return its ID."""
    result = connection.execute(
        """
        INSERT INTO services (slug, name)
        VALUES (%s, %s)
        ON CONFLICT (slug)
        DO UPDATE SET name = excluded.name
        RETURNING id
        """,
        (service_slug, service_name_from_slug(service_slug)),
    ).fetchone()
    return str(result[0])


def event_source_identity(event: NormalizedEvent) -> tuple[str, str, str, str]:
    """Return provider/source identity values for a normalized event."""
    if event.source_table == "aws_cloudwatch_alarm":
        return (
            "aws_cloudwatch",
            "cloudwatch_alarm",
            str(event.metadata["name"]),
            str(event.metadata["arn"]),
        )

    if event.source_table == "aws_cloudwatch_log_event":
        log_group_name = str(event.metadata["log_group_name"])
        return ("aws_cloudwatch", "cloudwatch_log_group", log_group_name, log_group_name)

    if event.source_table in {"github_repository_deployment", "github_pull_request"}:
        repository = str(event.metadata["repository_full_name"])
        return ("github", "github_repository", repository, repository)

    if event.source_table == "jira_issue":
        project_key = str(event.metadata["project_key"])
        return ("jira", "jira_project", project_key, project_key)

    raise ValueError(f"Unsupported source table: {event.source_table}")


def upsert_event_source(
    connection: Connection,
    service_id: str,
    event: NormalizedEvent,
) -> str:
    """Insert or update an event source and return its ID."""
    provider, source_type, name, external_reference = event_source_identity(event)
    result = connection.execute(
        """
        INSERT INTO event_sources (
          service_id,
          source_type,
          provider,
          name,
          external_reference,
          component,
          environment,
          metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (provider, source_type, external_reference)
        DO UPDATE SET
          service_id = excluded.service_id,
          name = excluded.name,
          component = excluded.component,
          environment = excluded.environment,
          metadata = excluded.metadata
        RETURNING id
        """,
        (
            service_id,
            source_type,
            provider,
            name,
            external_reference,
            event.component,
            event.environment,
            Jsonb(event.metadata),
        ),
    ).fetchone()
    return str(result[0])


def upsert_event(
    connection: Connection,
    service_id: str,
    event_source_id: str,
    event: NormalizedEvent,
) -> str:
    """Insert or update a normalized event and return its ID."""
    result = connection.execute(
        """
        INSERT INTO events (
          service_id,
          event_source_id,
          source_table,
          source_id,
          source_type,
          category,
          event_type,
          occurred_at,
          component,
          severity,
          summary,
          external_reference,
          correlation_identifiers,
          metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_table, source_id)
        DO UPDATE SET
          service_id = excluded.service_id,
          event_source_id = excluded.event_source_id,
          source_type = excluded.source_type,
          category = excluded.category,
          event_type = excluded.event_type,
          occurred_at = excluded.occurred_at,
          component = excluded.component,
          severity = excluded.severity,
          summary = excluded.summary,
          external_reference = excluded.external_reference,
          correlation_identifiers = excluded.correlation_identifiers,
          metadata = excluded.metadata
        RETURNING id
        """,
        (
            service_id,
            event_source_id,
            event.source_table,
            event.source_id,
            event.source_type,
            event.category,
            event.event_type,
            event.occurred_at,
            event.component,
            event.severity,
            event.summary,
            event.external_reference,
            Jsonb(event.correlation_identifiers),
            Jsonb(event.metadata),
        ),
    ).fetchone()
    return str(result[0])


def upsert_observation_procedure(connection: Connection) -> str:
    """Insert or update the first observation procedure and return its ID."""
    result = connection.execute(
        """
        INSERT INTO procedures (
          name,
          version,
          status,
          purpose,
          instructions,
          output_schema
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (name, version)
        DO UPDATE SET
          status = excluded.status,
          purpose = excluded.purpose,
          instructions = excluded.instructions,
          output_schema = excluded.output_schema
        RETURNING id
        """,
        (
            "operational_observation_v1",
            1,
            "active",
            "Generate evidence-backed operational observations for SREs.",
            OBSERVATION_PROCEDURE,
            Jsonb(OBSERVATION_OUTPUT_SCHEMA),
        ),
    ).fetchone()
    return str(result[0])


def load_seed_scenario(scenario: str, settings: Settings | None = None) -> SeedResult:
    """Load a fixture scenario into CockroachDB."""
    fixture_rows = load_fixture_rows(scenario)
    normalized_events = [normalize_row(source_table, row) for source_table, row in fixture_rows]

    service_ids: set[str] = set()
    event_source_ids: set[str] = set()
    event_ids: set[str] = set()

    with connect(settings) as connection:
        for event in normalized_events:
            service_id = upsert_service(connection, event.service_slug)
            event_source_id = upsert_event_source(connection, service_id, event)
            event_id = upsert_event(connection, service_id, event_source_id, event)

            service_ids.add(service_id)
            event_source_ids.add(event_source_id)
            event_ids.add(event_id)

        procedure_id = upsert_observation_procedure(connection)

    return SeedResult(
        scenario=scenario,
        services=len(service_ids),
        event_sources=len(event_source_ids),
        events=len(event_ids),
        procedures=1 if procedure_id else 0,
    )


def list_evidence(service_slug: str, settings: Settings | None = None) -> list[EvidenceRow]:
    """List normalized evidence for a service."""
    with connect(settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              e.id,
              s.slug AS service_slug,
              e.occurred_at,
              e.category,
              e.event_type,
              e.component,
              e.summary,
              e.source_table
            FROM events e
            JOIN services s ON s.id = e.service_id
            WHERE s.slug = %s
            ORDER BY e.occurred_at, e.source_table
            """,
            (service_slug,),
        )
        rows = cursor.fetchall()

    return [
        EvidenceRow(
            id=str(row["id"]),
            service_slug=str(row["service_slug"]),
            occurred_at=row["occurred_at"].isoformat(),
            category=str(row["category"]),
            event_type=str(row["event_type"]),
            component=str(row["component"]) if row["component"] else None,
            summary=str(row["summary"]),
            source_table=str(row["source_table"]),
        )
        for row in rows
    ]
