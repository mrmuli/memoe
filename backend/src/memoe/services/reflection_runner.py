"""Run model-backed reflection generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect
from memoe.providers.observations import ObservationRequest, ObservationResult
from memoe.services.observation_runner import (
    create_observation_provider,
    fetch_active_procedure,
    resolve_model_id,
)


@dataclass(frozen=True)
class ReflectionRunResult:
    """Summary of a completed reflection run."""

    run_id: str
    reflection_id: str
    statement: str
    confidence: float
    evidence_quality: dict[str, Any]
    details: dict[str, Any]
    supporting_observation_ids: list[str]
    rejected_observation_ids: list[str]
    limitations: list[str]


@dataclass(frozen=True)
class ReflectionSummary:
    """Compact reflection row for CLI lists."""

    id: str
    created_at: str
    model_id: str
    reflection_type: str
    confidence: float
    evidence_quality_rating: str
    statement: str


def run_reflection(
    provider_name: str,
    limit: int = 10,
    goal: str | None = None,
    service_scope: str | None = None,
    settings: Settings | None = None,
) -> ReflectionRunResult:
    """Run reflection generation over recent observations."""
    resolved_settings = settings or Settings()
    provider = create_observation_provider(provider_name, resolved_settings)
    model_id = resolve_model_id(provider_name, resolved_settings)

    with connect(resolved_settings) as connection:
        procedure = fetch_active_procedure(connection, "operational_reflection_v1")
        retrieval_results: list[dict[str, Any]] = []
        if goal:
            observations, prior_reflections, validation_results, retrieval_results = (
                fetch_goal_scoped_memory(
                    connection=connection,
                    goal=goal,
                    service_scope=service_scope,
                    limit=limit,
                    settings=resolved_settings,
                )
            )
        else:
            observations = fetch_observation_bundle(connection, limit)
            prior_reflections = fetch_prior_reflections(connection, limit=5)
            validation_results = fetch_validation_result_bundle(connection, limit=10)

        if not observations:
            raise ValueError("No observations found for reflection.")

        memory_bundle = build_memory_bundle(
            observations=observations,
            prior_reflections=prior_reflections,
            validation_results=validation_results,
            procedure=procedure,
            goal=goal,
            service_scope=service_scope,
            retrieval_results=retrieval_results,
        )
        request_payload = {
            "procedure": {
                "name": procedure["name"],
                "version": procedure["version"],
            },
            "memory_bundle": memory_bundle,
        }
        run_id = create_reflection_run(
            connection=connection,
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
        evidence=memory_bundle,
    )

    try:
        result = provider.generate_observation(request)
        validate_memory_ids(result, observations, prior_reflections, validation_results)
        validate_reflection_quality(result, observations)
        with connect(resolved_settings) as connection:
            reflection_id = persist_reflection_result(
                connection=connection,
                run_id=run_id,
                procedure_id=procedure["id"],
                result=result,
                observations=observations,
            )
    except Exception as error:
        with connect(resolved_settings) as connection:
            mark_reflection_run_failed(connection, run_id, str(error))
        raise

    return ReflectionRunResult(
        run_id=run_id,
        reflection_id=reflection_id,
        statement=result.statement,
        confidence=result.confidence,
        evidence_quality=result.evidence_quality,
        details=result.extra_fields,
        supporting_observation_ids=result.supporting_evidence_ids,
        rejected_observation_ids=result.rejected_evidence_ids,
        limitations=result.limitations,
    )


def fetch_observation_bundle(connection, limit: int) -> list[dict[str, Any]]:
    """Fetch latest observations as reflection evidence."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            WITH ranked_observations AS (
              SELECT
                o.id,
                s.slug AS service_slug,
                o.statement,
                o.observation_type,
                o.confidence,
                o.evidence_quality,
                o.lifecycle_status,
                o.valid_from,
                o.valid_until,
                o.stale_after,
                o.last_checked_at,
                o.limitations,
                o.reasoning_summary,
                o.created_at,
                r.provider,
                r.model_id,
                r.procedure_name,
                r.procedure_version,
                row_number() OVER (
                  PARTITION BY s.slug, r.model_id
                  ORDER BY o.created_at DESC
                ) AS rank
              FROM observations o
              JOIN services s ON s.id = o.service_id
              JOIN observation_runs r ON r.id = o.observation_run_id
            )
            SELECT
              id,
              service_slug,
              statement,
              observation_type,
              confidence,
              evidence_quality,
              lifecycle_status,
              valid_from,
              valid_until,
              stale_after,
              last_checked_at,
              limitations,
              reasoning_summary,
              created_at,
              provider,
              model_id,
              procedure_name,
              procedure_version
            FROM ranked_observations
            WHERE rank = 1
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()

    return [
        {
            "id": str(row["id"]),
            "service_slug": row["service_slug"],
            "statement": row["statement"],
            "observation_type": row["observation_type"],
            "confidence": float(row["confidence"]),
            "evidence_quality": row["evidence_quality"],
            "lifecycle_status": row["lifecycle_status"],
            "valid_from": row["valid_from"].isoformat(),
            "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
            "stale_after": row["stale_after"].isoformat() if row["stale_after"] else None,
            "last_checked_at": row["last_checked_at"].isoformat() if row["last_checked_at"] else None,
            "limitations": row["limitations"],
            "reasoning_summary": row["reasoning_summary"],
            "created_at": row["created_at"].isoformat(),
            "provider": row["provider"],
            "model_id": row["model_id"],
            "procedure_name": row["procedure_name"],
            "procedure_version": row["procedure_version"],
        }
        for row in rows
    ]


def fetch_goal_scoped_memory(
    connection,
    goal: str,
    service_scope: str | None,
    limit: int,
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch memory selected by vector search plus hybrid scoring."""
    from memoe.services.memory_embeddings import search_memory

    search_results = search_memory(
        goal=goal,
        service_scope=service_scope,
        limit=limit,
        settings=settings,
    )
    observation_ids = [
        row.memory_id for row in search_results if row.memory_type == "observation"
    ]
    reflection_ids = [
        row.memory_id for row in search_results if row.memory_type == "reflection"
    ]
    validation_result_ids = [
        row.memory_id for row in search_results if row.memory_type == "validation_result"
    ]

    observations = fetch_observations_by_ids(connection, observation_ids)
    if not observations:
        observations = fetch_observation_bundle(connection, min(limit, 5))

    return (
        observations,
        fetch_reflections_by_ids(connection, reflection_ids),
        fetch_validation_results_by_ids(connection, validation_result_ids),
        [
            {
                "memory_type": row.memory_type,
                "memory_id": row.memory_id,
                "hybrid_score": row.hybrid_score,
                "vector_similarity": row.vector_similarity,
                "service_slug": row.service_slug,
                "lifecycle_status": row.lifecycle_status,
                "evidence_quality_rating": row.evidence_quality_rating,
            }
            for row in search_results
        ],
    )


def fetch_observations_by_ids(connection, observation_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch observations by ID in newest-first order."""
    if not observation_ids:
        return []

    placeholders = ", ".join(["%s"] * len(observation_ids))
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            f"""
            SELECT
              o.id,
              s.slug AS service_slug,
              o.statement,
              o.observation_type,
              o.confidence,
              o.evidence_quality,
              o.lifecycle_status,
              o.valid_from,
              o.valid_until,
              o.stale_after,
              o.last_checked_at,
              o.limitations,
              o.reasoning_summary,
              o.created_at,
              r.provider,
              r.model_id,
              r.procedure_name,
              r.procedure_version
            FROM observations o
            JOIN services s ON s.id = o.service_id
            JOIN observation_runs r ON r.id = o.observation_run_id
            WHERE o.id IN ({placeholders})
            ORDER BY o.created_at DESC
            """,
            observation_ids,
        )
        rows = cursor.fetchall()

    return [
        {
            "id": str(row["id"]),
            "service_slug": row["service_slug"],
            "statement": row["statement"],
            "observation_type": row["observation_type"],
            "confidence": float(row["confidence"]),
            "evidence_quality": row["evidence_quality"],
            "lifecycle_status": row["lifecycle_status"],
            "valid_from": row["valid_from"].isoformat(),
            "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
            "stale_after": row["stale_after"].isoformat() if row["stale_after"] else None,
            "last_checked_at": row["last_checked_at"].isoformat() if row["last_checked_at"] else None,
            "limitations": row["limitations"],
            "reasoning_summary": row["reasoning_summary"],
            "created_at": row["created_at"].isoformat(),
            "provider": row["provider"],
            "model_id": row["model_id"],
            "procedure_name": row["procedure_name"],
            "procedure_version": row["procedure_version"],
        }
        for row in rows
    ]


def fetch_prior_reflections(connection, limit: int) -> list[dict[str, Any]]:
    """Fetch recent prior reflections as memory context."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              f.id,
              f.statement,
              f.reflection_type,
              f.confidence,
              f.evidence_quality,
              f.details,
              f.limitations,
              f.reasoning_summary,
              f.created_at,
              r.provider,
              r.model_id,
              r.procedure_name,
              r.procedure_version
            FROM reflections f
            JOIN reflection_runs r ON r.id = f.reflection_run_id
            ORDER BY f.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()

    return [
        {
            "id": str(row["id"]),
            "statement": row["statement"],
            "reflection_type": row["reflection_type"],
            "confidence": float(row["confidence"]),
            "evidence_quality": row["evidence_quality"],
            "details": row["details"],
            "limitations": row["limitations"],
            "reasoning_summary": row["reasoning_summary"],
            "created_at": row["created_at"].isoformat(),
            "provider": row["provider"],
            "model_id": row["model_id"],
            "procedure_name": row["procedure_name"],
            "procedure_version": row["procedure_version"],
        }
        for row in rows
    ]


def fetch_reflections_by_ids(connection, reflection_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch prior reflections by ID."""
    if not reflection_ids:
        return []

    placeholders = ", ".join(["%s"] * len(reflection_ids))
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            f"""
            SELECT
              f.id,
              f.statement,
              f.reflection_type,
              f.confidence,
              f.evidence_quality,
              f.details,
              f.limitations,
              f.reasoning_summary,
              f.created_at,
              r.provider,
              r.model_id,
              r.procedure_name,
              r.procedure_version
            FROM reflections f
            JOIN reflection_runs r ON r.id = f.reflection_run_id
            WHERE f.id IN ({placeholders})
            ORDER BY f.created_at DESC
            """,
            reflection_ids,
        )
        rows = cursor.fetchall()

    return [
        {
            "id": str(row["id"]),
            "statement": row["statement"],
            "reflection_type": row["reflection_type"],
            "confidence": float(row["confidence"]),
            "evidence_quality": row["evidence_quality"],
            "details": row["details"],
            "limitations": row["limitations"],
            "reasoning_summary": row["reasoning_summary"],
            "created_at": row["created_at"].isoformat(),
            "provider": row["provider"],
            "model_id": row["model_id"],
            "procedure_name": row["procedure_name"],
            "procedure_version": row["procedure_version"],
        }
        for row in rows
    ]


def fetch_validation_result_bundle(connection, limit: int) -> list[dict[str, Any]]:
    """Fetch recent validation results as memory context."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              id,
              observation_id,
              reflection_id,
              source,
              result_type,
              summary,
              evidence,
              created_at
            FROM validation_results
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()

    return [
        {
            "id": str(row["id"]),
            "observation_id": str(row["observation_id"]) if row["observation_id"] else None,
            "reflection_id": str(row["reflection_id"]) if row["reflection_id"] else None,
            "source": row["source"],
            "result_type": row["result_type"],
            "summary": row["summary"],
            "evidence": row["evidence"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


def fetch_validation_results_by_ids(connection, validation_result_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch validation results by ID."""
    if not validation_result_ids:
        return []

    placeholders = ", ".join(["%s"] * len(validation_result_ids))
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            f"""
            SELECT
              id,
              observation_id,
              reflection_id,
              source,
              result_type,
              summary,
              evidence,
              created_at
            FROM validation_results
            WHERE id IN ({placeholders})
            ORDER BY created_at DESC
            """,
            validation_result_ids,
        )
        rows = cursor.fetchall()

    return [
        {
            "id": str(row["id"]),
            "observation_id": str(row["observation_id"]) if row["observation_id"] else None,
            "reflection_id": str(row["reflection_id"]) if row["reflection_id"] else None,
            "source": row["source"],
            "result_type": row["result_type"],
            "summary": row["summary"],
            "evidence": row["evidence"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


def build_memory_bundle(
    observations: list[dict[str, Any]],
    prior_reflections: list[dict[str, Any]],
    validation_results: list[dict[str, Any]],
    procedure: dict[str, Any],
    goal: str | None = None,
    service_scope: str | None = None,
    retrieval_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build the cross-memory input sent to the reflection provider."""
    return [
        {
            "id": "memory:active_reflection_procedure",
            "memory_type": "procedure",
            "name": procedure["name"],
            "version": procedure["version"],
            "purpose": "Current procedural memory guiding reflection.",
            "goal": goal,
            "service_scope": service_scope,
        },
        {
            "id": "memory:retrieval_results",
            "memory_type": "retrieval_result_set",
            "items": retrieval_results or [],
        },
        {
            "id": "memory:latest_observations",
            "memory_type": "observation_set",
            "items": observations,
        },
        {
            "id": "memory:prior_reflections",
            "memory_type": "reflection_set",
            "items": prior_reflections,
        },
        {
            "id": "memory:validation_results",
            "memory_type": "validation_result_set",
            "items": validation_results,
        },
    ]


def create_reflection_run(
    connection,
    procedure: dict[str, Any],
    provider: str,
    model_id: str,
    request_payload: dict[str, Any],
) -> str:
    """Create a running reflection run record."""
    row = connection.execute(
        """
        INSERT INTO reflection_runs (
          procedure_id,
          procedure_name,
          procedure_version,
          provider,
          model_id,
          status,
          request_payload
        )
        VALUES (%s, %s, %s, %s, %s, 'running', %s)
        RETURNING id
        """,
        (
            procedure["id"],
            procedure["name"],
            procedure["version"],
            provider,
            model_id,
            Jsonb(request_payload),
        ),
    ).fetchone()
    return str(row[0])


def validate_memory_ids(
    result: ObservationResult,
    observations: list[dict[str, Any]],
    prior_reflections: list[dict[str, Any]],
    validation_results: list[dict[str, Any]],
) -> None:
    """Ensure model-returned memory IDs exist in the request bundle."""
    known_ids = (
        {row["id"] for row in observations}
        | {row["id"] for row in prior_reflections}
        | {row["id"] for row in validation_results}
    )
    returned_ids = set(result.supporting_evidence_ids) | set(result.rejected_evidence_ids)
    unknown_ids = sorted(returned_ids - known_ids)
    if unknown_ids:
        raise ValueError(f"Model returned unknown memory IDs: {', '.join(unknown_ids)}")


def validate_reflection_quality(result: ObservationResult, observations: list[dict[str, Any]]) -> None:
    """Reject reflection outputs that overclaim from narrow evidence."""
    observations_by_id = {row["id"]: row for row in observations}
    supporting_observations = [
        observations_by_id[observation_id]
        for observation_id in result.supporting_evidence_ids
        if observation_id in observations_by_id
    ]
    supporting_services = {row["service_slug"] for row in supporting_observations}
    statement = result.statement.lower()
    causal_overclaim_terms = (
        " caused ",
        " can introduce",
        " can trigger",
        " will cause",
        " triggered ",
        " is a pattern",
    )
    hypothesis_terms = ("hypothesis", "may indicate", "may suggest")

    if (
        len(supporting_services) <= 1
        and any(term in statement for term in causal_overclaim_terms)
        and not any(term in statement for term in hypothesis_terms)
    ):
        raise ValueError(
            "Reflection overclaimed from single-service evidence. "
            "Single-service reflections must be framed as a hypothesis."
        )


def persist_reflection_result(
    connection,
    run_id: str,
    procedure_id: str,
    result: ObservationResult,
    observations: list[dict[str, Any]],
) -> str:
    """Persist a successful reflection and observation links."""
    reflection_row = connection.execute(
        """
        INSERT INTO reflections (
          reflection_run_id,
          procedure_id,
          statement,
          reflection_type,
          confidence,
          evidence_quality,
          details,
          limitations,
          reasoning_summary
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            run_id,
            procedure_id,
            result.statement,
            result.observation_type,
            result.confidence,
            Jsonb(result.evidence_quality),
            Jsonb(result.extra_fields),
            Jsonb(result.limitations),
            result.reasoning_summary,
        ),
    ).fetchone()
    reflection_id = str(reflection_row[0])

    connection.execute(
        """
        UPDATE reflection_runs
        SET status = 'completed', raw_response = %s, completed_at = now()
        WHERE id = %s
        """,
        (Jsonb(result.raw_output), run_id),
    )

    observation_by_id = {row["id"]: row for row in observations}
    for observation_id in observation_by_id:
        insert_reflection_observation(connection, run_id, reflection_id, observation_id, "considered")
    for observation_id in result.supporting_evidence_ids:
        if observation_id in observation_by_id:
            insert_reflection_observation(connection, run_id, reflection_id, observation_id, "supporting")
    for observation_id in result.rejected_evidence_ids:
        if observation_id in observation_by_id:
            insert_reflection_observation(connection, run_id, reflection_id, observation_id, "rejected")

    return reflection_id


def insert_reflection_observation(
    connection,
    run_id: str,
    reflection_id: str,
    observation_id: str,
    role: str,
) -> None:
    """Insert one reflection-observation link."""
    connection.execute(
        """
        INSERT INTO reflection_observations (
          reflection_run_id,
          reflection_id,
          observation_id,
          role
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (reflection_run_id, observation_id, role)
        DO NOTHING
        """,
        (run_id, reflection_id, observation_id, role),
    )


def mark_reflection_run_failed(connection, run_id: str, error_message: str) -> None:
    """Mark a reflection run as failed."""
    connection.execute(
        """
        UPDATE reflection_runs
        SET status = 'failed', error_message = %s, completed_at = now()
        WHERE id = %s
        """,
        (error_message, run_id),
    )


def list_reflections(
    limit: int = 10,
    settings: Settings | None = None,
) -> list[ReflectionSummary]:
    """List recent stored reflections."""
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              f.id,
              f.created_at,
              r.model_id,
              f.reflection_type,
              f.confidence,
              COALESCE(f.evidence_quality->>'rating', 'unknown') AS evidence_quality_rating,
              f.statement
            FROM reflections f
            JOIN reflection_runs r ON r.id = f.reflection_run_id
            ORDER BY f.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()

    return [
        ReflectionSummary(
            id=str(row["id"]),
            created_at=row["created_at"].isoformat(),
            model_id=str(row["model_id"]),
            reflection_type=str(row["reflection_type"]),
            confidence=float(row["confidence"]),
            evidence_quality_rating=str(row["evidence_quality_rating"]),
            statement=str(row["statement"]),
        )
        for row in rows
    ]
