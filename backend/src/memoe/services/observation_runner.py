"""Run model-backed observation generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect
from memoe.providers.observations import ObservationRequest, ObservationResult
from memoe.providers.ollama import OllamaObservationProvider


@dataclass(frozen=True)
class ObservationRunResult:
    """Summary of a completed observation run."""

    run_id: str
    observation_id: str
    statement: str
    confidence: float
    evidence_quality: dict[str, Any]
    supporting_evidence_ids: list[str]
    rejected_evidence_ids: list[str]
    limitations: list[str]


@dataclass(frozen=True)
class StoredObservation:
    """Stored observation for CLI display."""

    id: str
    service_slug: str
    statement: str
    observation_type: str
    confidence: float
    evidence_quality: dict[str, Any]
    limitations: list[str]
    reasoning_summary: str
    model_id: str
    procedure_name: str
    procedure_version: int
    evidence: list[dict[str, Any]]


@dataclass(frozen=True)
class ObservationSummary:
    """Compact observation row for CLI lists."""

    id: str
    service_slug: str
    created_at: str
    model_id: str
    observation_type: str
    confidence: float
    evidence_quality_rating: str
    statement: str


def run_observation(
    service_slug: str,
    provider_name: str,
    settings: Settings | None = None,
) -> ObservationRunResult:
    """Run observation generation for one service."""
    resolved_settings = settings or Settings()
    if provider_name != "ollama":
        raise ValueError(f"Unsupported observation provider: {provider_name}")

    with connect(resolved_settings) as connection:
        service = fetch_service(connection, service_slug)
        procedure = fetch_active_procedure(connection, "operational_observation_v1")
        evidence = fetch_evidence_bundle(connection, service["id"])

        if not evidence:
            raise ValueError(f"No evidence found for service: {service_slug}")

        request_payload = {
            "procedure": {
                "name": procedure["name"],
                "version": procedure["version"],
            },
            "evidence": evidence,
        }
        run_id = create_observation_run(
            connection=connection,
            service_id=service["id"],
            procedure=procedure,
            provider=provider_name,
            model_id=resolved_settings.ollama_model or "unknown",
            request_payload=request_payload,
        )

    provider = OllamaObservationProvider(resolved_settings)
    request = ObservationRequest(
        procedure_name=str(procedure["name"]),
        procedure_version=int(procedure["version"]),
        procedure_instructions=str(procedure["instructions"]),
        output_schema=dict(procedure["output_schema"]),
        evidence=evidence,
    )

    try:
        result = provider.generate_observation(request)
        validate_evidence_ids(result, evidence)
        with connect(resolved_settings) as connection:
            observation_id = persist_observation_result(
                connection=connection,
                run_id=run_id,
                service_id=service["id"],
                procedure_id=procedure["id"],
                result=result,
                evidence=evidence,
            )
    except Exception as error:
        with connect(resolved_settings) as connection:
            mark_observation_run_failed(connection, run_id, str(error))
        raise

    return ObservationRunResult(
        run_id=run_id,
        observation_id=observation_id,
        statement=result.statement,
        confidence=result.confidence,
        evidence_quality=result.evidence_quality,
        supporting_evidence_ids=result.supporting_evidence_ids,
        rejected_evidence_ids=result.rejected_evidence_ids,
        limitations=result.limitations,
    )


def fetch_service(connection, service_slug: str) -> dict[str, Any]:
    """Fetch one service by slug."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id, slug FROM services WHERE slug = %s", (service_slug,))
        row = cursor.fetchone()
    if not row:
        raise ValueError(f"Unknown service: {service_slug}")
    return dict(row)


def fetch_active_procedure(connection, procedure_name: str) -> dict[str, Any]:
    """Fetch the active version of a procedure."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, name, version, instructions, output_schema
            FROM procedures
            WHERE name = %s AND status = 'active'
            ORDER BY version DESC
            LIMIT 1
            """,
            (procedure_name,),
        )
        row = cursor.fetchone()
    if not row:
        raise ValueError(f"Active procedure not found: {procedure_name}")
    return dict(row)


def fetch_evidence_bundle(connection, service_id: str) -> list[dict[str, Any]]:
    """Fetch evidence for one service in chronological order."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              id,
              source_table,
              category,
              event_type,
              occurred_at,
              component,
              severity,
              summary,
              external_reference,
              correlation_identifiers,
              metadata
            FROM events
            WHERE service_id = %s
            ORDER BY occurred_at, source_table
            """,
            (service_id,),
        )
        rows = cursor.fetchall()

    return [
        {
            "id": str(row["id"]),
            "source_table": row["source_table"],
            "category": row["category"],
            "event_type": row["event_type"],
            "occurred_at": row["occurred_at"].isoformat(),
            "component": row["component"],
            "severity": row["severity"],
            "summary": row["summary"],
            "external_reference": row["external_reference"],
            "correlation_identifiers": row["correlation_identifiers"],
            "metadata": row["metadata"],
        }
        for row in rows
    ]


def create_observation_run(
    connection,
    service_id: str,
    procedure: dict[str, Any],
    provider: str,
    model_id: str,
    request_payload: dict[str, Any],
) -> str:
    """Create a running observation run record."""
    row = connection.execute(
        """
        INSERT INTO observation_runs (
          service_id,
          procedure_id,
          procedure_name,
          procedure_version,
          provider,
          model_id,
          status,
          request_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'running', %s)
        RETURNING id
        """,
        (
            service_id,
            procedure["id"],
            procedure["name"],
            procedure["version"],
            provider,
            model_id,
            Jsonb(request_payload),
        ),
    ).fetchone()
    return str(row[0])


def validate_evidence_ids(result: ObservationResult, evidence: list[dict[str, Any]]) -> None:
    """Ensure model-returned evidence IDs exist in the request evidence bundle."""
    known_ids = {row["id"] for row in evidence}
    returned_ids = set(result.supporting_evidence_ids) | set(result.rejected_evidence_ids)
    unknown_ids = sorted(returned_ids - known_ids)
    if unknown_ids:
        raise ValueError(f"Model returned unknown evidence IDs: {', '.join(unknown_ids)}")


def persist_observation_result(
    connection,
    run_id: str,
    service_id: str,
    procedure_id: str,
    result: ObservationResult,
    evidence: list[dict[str, Any]],
) -> str:
    """Persist a successful observation and evidence links."""
    observation_row = connection.execute(
        """
        INSERT INTO observations (
          service_id,
          observation_run_id,
          procedure_id,
          statement,
          observation_type,
          confidence,
          evidence_quality,
          limitations,
          reasoning_summary
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            service_id,
            run_id,
            procedure_id,
            result.statement,
            result.observation_type,
            result.confidence,
            Jsonb(result.evidence_quality),
            Jsonb(result.limitations),
            result.reasoning_summary,
        ),
    ).fetchone()
    observation_id = str(observation_row[0])

    connection.execute(
        """
        UPDATE observation_runs
        SET status = 'completed', raw_response = %s, completed_at = now()
        WHERE id = %s
        """,
        (Jsonb(result.raw_output), run_id),
    )

    evidence_by_id = {row["id"]: row for row in evidence}
    for event_id in evidence_by_id:
        insert_observation_evidence(connection, run_id, observation_id, event_id, "considered")
    for event_id in result.supporting_evidence_ids:
        insert_observation_evidence(connection, run_id, observation_id, event_id, "supporting")
    for event_id in result.rejected_evidence_ids:
        insert_observation_evidence(connection, run_id, observation_id, event_id, "rejected")

    return observation_id


def insert_observation_evidence(
    connection,
    run_id: str,
    observation_id: str,
    event_id: str,
    role: str,
) -> None:
    """Insert one observation evidence link."""
    connection.execute(
        """
        INSERT INTO observation_evidence (observation_run_id, observation_id, event_id, role)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (observation_run_id, event_id, role)
        DO NOTHING
        """,
        (run_id, observation_id, event_id, role),
    )


def mark_observation_run_failed(connection, run_id: str, error_message: str) -> None:
    """Mark an observation run as failed."""
    connection.execute(
        """
        UPDATE observation_runs
        SET status = 'failed', error_message = %s, completed_at = now()
        WHERE id = %s
        """,
        (error_message, run_id),
    )


def latest_observation(settings: Settings | None = None) -> StoredObservation | None:
    """Fetch the latest stored observation."""
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT
                  o.id,
                  s.slug AS service_slug,
                  o.statement,
                  o.observation_type,
                  o.confidence,
                  o.evidence_quality,
                  o.limitations,
                  o.reasoning_summary,
                  r.model_id,
                  r.procedure_name,
                  r.procedure_version
                FROM observations o
                JOIN services s ON s.id = o.service_id
                JOIN observation_runs r ON r.id = o.observation_run_id
                ORDER BY o.created_at DESC
                LIMIT 1
                """
            )
            observation = cursor.fetchone()
            if not observation:
                return None

            cursor.execute(
                """
                SELECT
                  oe.role,
                  e.id,
                  e.occurred_at,
                  e.category,
                  e.event_type,
                  e.component,
                  e.summary,
                  e.source_table
                FROM observation_evidence oe
                JOIN events e ON e.id = oe.event_id
                WHERE oe.observation_id = %s
                ORDER BY e.occurred_at, oe.role
                """,
                (observation["id"],),
            )
            evidence = [dict(row) for row in cursor.fetchall()]

    return StoredObservation(
        id=str(observation["id"]),
        service_slug=str(observation["service_slug"]),
        statement=str(observation["statement"]),
        observation_type=str(observation["observation_type"]),
        confidence=float(observation["confidence"]),
        evidence_quality=dict(observation["evidence_quality"]),
        limitations=list(observation["limitations"]),
        reasoning_summary=str(observation["reasoning_summary"]),
        model_id=str(observation["model_id"]),
        procedure_name=str(observation["procedure_name"]),
        procedure_version=int(observation["procedure_version"]),
        evidence=[
            {
                **row,
                "id": str(row["id"]),
                "occurred_at": row["occurred_at"].isoformat(),
            }
            for row in evidence
        ],
    )


def list_observations(
    service_slug: str | None = None,
    limit: int = 10,
    settings: Settings | None = None,
) -> list[ObservationSummary]:
    """List recent stored observations."""
    resolved_settings = settings or Settings()
    query = """
        SELECT
          o.id,
          s.slug AS service_slug,
          o.created_at,
          r.model_id,
          o.observation_type,
          o.confidence,
          COALESCE(o.evidence_quality->>'rating', 'unknown') AS evidence_quality_rating,
          o.statement
        FROM observations o
        JOIN services s ON s.id = o.service_id
        JOIN observation_runs r ON r.id = o.observation_run_id
    """
    params: list[Any] = []
    if service_slug:
        query += " WHERE s.slug = %s"
        params.append(service_slug)

    query += " ORDER BY o.created_at DESC LIMIT %s"
    params.append(limit)

    with connect(resolved_settings) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

    return [
        ObservationSummary(
            id=str(row["id"]),
            service_slug=str(row["service_slug"]),
            created_at=row["created_at"].isoformat(),
            model_id=str(row["model_id"]),
            observation_type=str(row["observation_type"]),
            confidence=float(row["confidence"]),
            evidence_quality_rating=str(row["evidence_quality_rating"]),
            statement=str(row["statement"]),
        )
        for row in rows
    ]
