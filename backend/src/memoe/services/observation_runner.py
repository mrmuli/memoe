"""Run model-backed observation generation."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect
from memoe.providers.bedrock import BedrockObservationProvider
from memoe.providers.observations import ObservationRequest, ObservationResult
from memoe.providers.ollama import OllamaObservationProvider

logger = logging.getLogger(__name__)


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
    lifecycle_status: str
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
    lifecycle_status: str
    statement: str
    occurrence_count: int
    first_seen_at: str
    last_seen_at: str


def run_observation(
    service_slug: str,
    provider_name: str,
    settings: Settings | None = None,
) -> ObservationRunResult:
    """Run observation generation for one service."""
    resolved_settings = settings or Settings()
    provider = create_observation_provider(provider_name, resolved_settings)
    model_id = resolve_model_id(provider_name, resolved_settings)
    logger.info(
        "observation_run.start service_slug=%s provider=%s model_id=%s",
        service_slug,
        provider_name,
        model_id,
    )

    with connect(resolved_settings) as connection:
        service = fetch_service(connection, service_slug)
        procedure = fetch_active_procedure(connection, "operational_observation_v1")
        evidence = fetch_evidence_bundle(connection, service["id"])

        if not evidence:
            raise ValueError(f"No evidence found for service: {service_slug}")

        evidence_summary = summarize_evidence_for_logs(evidence)
        logger.info(
            "observation_run.evidence_loaded service_slug=%s evidence_summary=%s",
            service_slug,
            evidence_summary,
        )

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
            model_id=model_id,
            request_payload=request_payload,
        )

    request = ObservationRequest(
        procedure_name=str(procedure["name"]),
        procedure_version=int(procedure["version"]),
        procedure_instructions=str(procedure["instructions"]),
        output_schema=dict(procedure["output_schema"]),
        evidence=evidence,
    )

    try:
        logger.info(
            "observation_run.provider_request run_id=%s service_slug=%s procedure=%s:%s "
            "provider=%s model_id=%s evidence_summary=%s",
            run_id,
            service_slug,
            request.procedure_name,
            request.procedure_version,
            provider_name,
            model_id,
            evidence_summary,
        )
        result = provider.generate_observation(request)
        validate_evidence_ids(result, evidence)
        with connect(resolved_settings) as connection:
            observation_id = persist_observation_result(
                connection=connection,
                run_id=run_id,
                service_id=service["id"],
                service_slug=str(service["slug"]),
                procedure_id=procedure["id"],
                result=result,
                evidence=evidence,
            )
        logger.info(
            "observation_run.completed run_id=%s observation_id=%s service_slug=%s "
            "confidence=%s evidence_quality=%s supporting_evidence=%s rejected_evidence=%s",
            run_id,
            observation_id,
            service_slug,
            result.confidence,
            result.evidence_quality.get("rating", "unknown"),
            len(result.supporting_evidence_ids),
            len(result.rejected_evidence_ids),
        )
    except Exception as error:
        with connect(resolved_settings) as connection:
            mark_observation_run_failed(connection, run_id, str(error))
        logger.exception(
            "observation_run.failed run_id=%s service_slug=%s provider=%s",
            run_id,
            service_slug,
            provider_name,
        )
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


def resolve_model_id(provider_name: str, settings: Settings) -> str:
    """Return the configured model ID for a provider."""
    if provider_name == "ollama":
        return settings.ollama_model or "unknown"
    if provider_name == "bedrock":
        return settings.bedrock_model_id or "unknown"

    raise ValueError(f"Unsupported observation provider: {provider_name}")


def create_observation_provider(provider_name: str, settings: Settings):
    """Create an observation provider from its CLI/config name."""
    if provider_name == "ollama":
        return OllamaObservationProvider(settings)
    if provider_name == "bedrock":
        return BedrockObservationProvider(settings)

    raise ValueError(f"Unsupported observation provider: {provider_name}")


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
    service_slug: str,
    procedure_id: str,
    result: ObservationResult,
    evidence: list[dict[str, Any]],
) -> str:
    """Persist a successful observation and evidence links."""
    signature = observation_signature(
        service_slug=service_slug,
        observation_type=result.observation_type,
        statement=result.statement,
        supporting_ids=result.supporting_evidence_ids,
    )
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
          details,
          limitations,
          reasoning_summary,
          signature,
          occurrence_count,
          first_seen_at,
          last_seen_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, now(), now())
        ON CONFLICT (signature)
        DO UPDATE SET
          occurrence_count = observations.occurrence_count + 1,
          last_seen_at = now(),
          confidence = greatest(observations.confidence, excluded.confidence),
          evidence_quality = excluded.evidence_quality,
          details = excluded.details,
          limitations = excluded.limitations,
          reasoning_summary = excluded.reasoning_summary,
          lifecycle_status = 'fresh'
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
            Jsonb(result.extra_fields),
            Jsonb(result.limitations),
            result.reasoning_summary,
            signature,
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


def summarize_evidence_for_logs(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize observation evidence without logging raw source payload content."""
    occurred_at_values = sorted(str(row["occurred_at"]) for row in evidence if row.get("occurred_at"))
    return {
        "total": len(evidence),
        "source_tables": compact_counter(row.get("source_table") for row in evidence),
        "categories": compact_counter(row.get("category") for row in evidence),
        "event_types": compact_counter(row.get("event_type") for row in evidence),
        "components": compact_counter(row.get("component") for row in evidence),
        "time_range": {
            "start": occurred_at_values[0] if occurred_at_values else None,
            "end": occurred_at_values[-1] if occurred_at_values else None,
        },
    }


def compact_counter(values, limit: int = 8) -> dict[str, int]:
    """Count non-empty values and cap the number of keys shown in logs."""
    counter = Counter(str(value) for value in values if value)
    most_common = dict(counter.most_common(limit))
    remaining = sum(counter.values()) - sum(most_common.values())
    if remaining:
        most_common["other"] = remaining
    return most_common


def observation_signature(
    service_slug: str,
    observation_type: str,
    statement: str,
    supporting_ids: list[str],
) -> str:
    """Build a stable signature for deduplicating repeated observations."""
    evidence_key = " ".join(sorted(supporting_ids))
    signature_source = f"{service_slug} {observation_type} {evidence_key or statement}"
    normalized = re.sub(r"[^a-z0-9]+", " ", signature_source.lower())
    compact = " ".join(normalized.split())
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


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
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              o.id,
              s.slug AS service_slug,
              o.statement,
              o.observation_type,
              o.confidence,
              o.evidence_quality,
              o.lifecycle_status,
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
        lifecycle_status=str(observation["lifecycle_status"]),
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
          o.lifecycle_status,
          o.statement,
          o.signature,
          o.occurrence_count,
          o.first_seen_at,
          o.last_seen_at
        FROM observations o
        JOIN services s ON s.id = o.service_id
        JOIN observation_runs r ON r.id = o.observation_run_id
    """
    params: list[Any] = []
    if service_slug:
        query += " WHERE s.slug = %s"
        params.append(service_slug)

    query += " ORDER BY o.last_seen_at DESC LIMIT %s"
    params.append(limit * 3)

    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        supporting_ids_by_observation = fetch_supporting_event_ids(
            cursor,
            [str(row["id"]) for row in rows if not row["signature"]],
        )

    summaries: list[ObservationSummary] = []
    seen_signatures: set[str] = set()
    for row in rows:
        signature = row["signature"] or observation_signature(
            service_slug=str(row["service_slug"]),
            observation_type=str(row["observation_type"]),
            statement=str(row["statement"]),
            supporting_ids=supporting_ids_by_observation.get(str(row["id"]), []),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        summaries.append(
            ObservationSummary(
                id=str(row["id"]),
                service_slug=str(row["service_slug"]),
                created_at=row["created_at"].isoformat(),
                model_id=str(row["model_id"]),
                observation_type=str(row["observation_type"]),
                confidence=float(row["confidence"]),
                evidence_quality_rating=str(row["evidence_quality_rating"]),
                lifecycle_status=str(row["lifecycle_status"]),
                statement=str(row["statement"]),
                occurrence_count=int(row["occurrence_count"]),
                first_seen_at=row["first_seen_at"].isoformat(),
                last_seen_at=row["last_seen_at"].isoformat(),
            )
        )
        if len(summaries) >= limit:
            break

    return summaries


def fetch_supporting_event_ids(cursor, observation_ids: list[str]) -> dict[str, list[str]]:
    """Fetch supporting event IDs for legacy observations that do not have signatures."""
    if not observation_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(observation_ids))
    cursor.execute(
        f"""
        SELECT observation_id, event_id
        FROM observation_evidence
        WHERE role = 'supporting'
          AND observation_id IN ({placeholders})
        ORDER BY event_id
        """,
        observation_ids,
    )
    supporting_ids_by_observation: dict[str, list[str]] = {}
    for row in cursor.fetchall():
        observation_id = str(row["observation_id"])
        supporting_ids_by_observation.setdefault(observation_id, []).append(str(row["event_id"]))
    return supporting_ids_by_observation
