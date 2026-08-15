"""Embed and search Memoe memory with CockroachDB vectors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
import httpx
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect

EMBEDDING_DIMENSIONS = 256
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
BGE_EMBEDDING_DIMENSIONS = 384
BGE_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_\-:.]*")


@dataclass(frozen=True)
class EmbeddingRefreshResult:
    """Summary of refreshed memory embeddings."""

    embedded: int
    observations: int
    reflections: int
    validation_results: int


@dataclass(frozen=True)
class MemorySearchResult:
    """Hybrid-scored memory search hit."""

    memory_type: str
    memory_id: str
    vector_similarity: float
    hybrid_score: float
    service_slug: str | None
    lifecycle_status: str | None
    evidence_quality_rating: str | None
    created_at: str | None
    text: str


def refresh_memory_embeddings(settings: Settings | None = None) -> EmbeddingRefreshResult:
    """Refresh embeddings for observations, reflections, and validation results."""
    resolved_settings = settings or Settings()
    embedded = 0
    counts = {"observation": 0, "reflection": 0, "validation_result": 0}

    with connect(resolved_settings) as connection:
        rows = fetch_memory_rows(connection)
        for row in rows:
            embedding = embed_text(row["embedded_text"], resolved_settings)
            upsert_memory_embedding(
                connection=connection,
                memory_type=row["memory_type"],
                memory_id=row["memory_id"],
                embedded_text=row["embedded_text"],
                embedding=embedding,
                metadata=row["metadata"],
                settings=resolved_settings,
            )
            embedded += 1
            counts[row["memory_type"]] += 1

    return EmbeddingRefreshResult(
        embedded=embedded,
        observations=counts["observation"],
        reflections=counts["reflection"],
        validation_results=counts["validation_result"],
    )


def fetch_memory_rows(connection) -> list[dict[str, Any]]:
    """Fetch memory rows that should have embeddings."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              'observation' AS memory_type,
              o.id AS memory_id,
              concat_ws(
                ' ',
                s.slug,
                o.observation_type,
                o.statement,
                o.reasoning_summary,
                o.evidence_quality::STRING,
                o.limitations::STRING
              ) AS embedded_text,
              jsonb_build_object(
                'service_slug', s.slug,
                'lifecycle_status', o.lifecycle_status,
                'evidence_quality_rating', COALESCE(o.evidence_quality->>'rating', 'unknown'),
                'created_at', o.created_at::STRING
              ) AS metadata
            FROM observations o
            JOIN services s ON s.id = o.service_id

            UNION ALL

            SELECT
              'reflection' AS memory_type,
              f.id AS memory_id,
              concat_ws(
                ' ',
                f.reflection_type,
                f.statement,
                f.reasoning_summary,
                f.evidence_quality::STRING,
                f.details::STRING,
                f.limitations::STRING
              ) AS embedded_text,
              jsonb_build_object(
                'service_slug', NULL,
                'lifecycle_status', 'fresh',
                'evidence_quality_rating', COALESCE(f.evidence_quality->>'rating', 'unknown'),
                'created_at', f.created_at::STRING
              ) AS metadata
            FROM reflections f

            UNION ALL

            SELECT
              'validation_result' AS memory_type,
              vr.id AS memory_id,
              concat_ws(
                ' ',
                vr.source,
                vr.result_type,
                vr.summary,
                vr.evidence::STRING
              ) AS embedded_text,
              jsonb_build_object(
                'service_slug', NULL,
                'lifecycle_status', vr.result_type,
                'evidence_quality_rating', 'validation',
                'created_at', vr.created_at::STRING
              ) AS metadata
            FROM validation_results vr
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def upsert_memory_embedding(
    connection,
    memory_type: str,
    memory_id: str,
    embedded_text: str,
    embedding: list[float],
    metadata: dict[str, Any],
    settings: Settings,
) -> None:
    """Store one memory embedding."""
    dimensions = embedding_dimensions(settings)
    model_id = embedding_model_id(settings)
    connection.execute(
        f"""
        INSERT INTO memory_embeddings (
          memory_type,
          memory_id,
          embedding_model,
          {vector_column(dimensions)},
          embedded_text,
          metadata
        )
        VALUES (%s, %s, %s, %s::VECTOR({dimensions}), %s, %s)
        ON CONFLICT (memory_type, memory_id, embedding_model)
        DO UPDATE SET
          embedding = {excluded_vector_value("embedding", dimensions)},
          embedding_384 = {excluded_vector_value("embedding_384", dimensions)},
          embedded_text = excluded.embedded_text,
          metadata = excluded.metadata,
          updated_at = now()
        """,
        (
            memory_type,
            memory_id,
            model_id,
            vector_literal(embedding),
            embedded_text,
            Jsonb(metadata),
        ),
    )


def search_memory(
    goal: str,
    service_scope: str | None = None,
    limit: int = 10,
    settings: Settings | None = None,
) -> list[MemorySearchResult]:
    """Search memory with vector similarity plus simple metadata scoring."""
    resolved_settings = settings or Settings()
    dimensions = embedding_dimensions(resolved_settings)
    model_id = embedding_model_id(resolved_settings)
    query_embedding = vector_literal(embed_query(goal, resolved_settings))
    candidate_limit = max(limit * 4, 20)

    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            f"""
            SELECT
              memory_type,
              memory_id,
              embedded_text,
              metadata,
              1 - ({vector_column(dimensions)} <=> %s::VECTOR({dimensions})) AS vector_similarity
            FROM memory_embeddings
            WHERE embedding_model = %s
              AND {vector_column(dimensions)} IS NOT NULL
            ORDER BY {vector_column(dimensions)} <=> %s::VECTOR({dimensions})
            LIMIT %s
            """,
            (query_embedding, model_id, query_embedding, candidate_limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]

    scored = [score_memory_row(row, goal, service_scope) for row in rows]
    scored.sort(key=lambda row: row.hybrid_score, reverse=True)
    return scored[:limit]


def score_memory_row(
    row: dict[str, Any],
    goal: str,
    service_scope: str | None,
) -> MemorySearchResult:
    """Apply hybrid scoring to one vector search row."""
    metadata = dict(row["metadata"])
    vector_similarity = float(row["vector_similarity"])
    service_slug = metadata.get("service_slug")
    lifecycle_status = metadata.get("lifecycle_status")
    evidence_quality_rating = metadata.get("evidence_quality_rating")
    text = str(row["embedded_text"])

    score = vector_similarity
    if service_scope and service_slug == service_scope:
        score += 0.25
    elif service_scope and service_slug and service_slug != service_scope:
        score -= 0.15

    score += lifecycle_score(lifecycle_status)
    score += evidence_quality_score(evidence_quality_rating)
    score += keyword_score(goal, text)
    score += recency_score(metadata.get("created_at"))

    return MemorySearchResult(
        memory_type=str(row["memory_type"]),
        memory_id=str(row["memory_id"]),
        vector_similarity=round(vector_similarity, 4),
        hybrid_score=round(score, 4),
        service_slug=str(service_slug) if service_slug else None,
        lifecycle_status=str(lifecycle_status) if lifecycle_status else None,
        evidence_quality_rating=str(evidence_quality_rating) if evidence_quality_rating else None,
        created_at=str(metadata["created_at"]) if metadata.get("created_at") else None,
        text=text,
    )


def embed_text(text: str, settings: Settings) -> list[float]:
    """Generate an embedding with the configured provider."""
    provider = settings.embedding_provider.lower()
    if provider == "bedrock":
        return embed_text_with_bedrock(text, settings)
    if provider == "tei":
        return embed_text_with_tei(text, settings, input_type="passage")

    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def embed_query(text: str, settings: Settings) -> list[float]:
    """Generate a query embedding with the configured provider."""
    provider = settings.embedding_provider.lower()
    if provider == "bedrock":
        return embed_text_with_bedrock(text, settings)
    if provider == "tei":
        return embed_text_with_tei(text, settings, input_type="query")

    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def embed_text_with_bedrock(text: str, settings: Settings) -> list[float]:
    """Generate a Titan text embedding with Bedrock."""
    body = {
        "inputText": text,
        "dimensions": embedding_dimensions(settings),
        "normalize": settings.bedrock_embedding_normalize,
    }
    response = bedrock_runtime_client(settings).invoke_model(
        modelId=embedding_model_id(settings),
        body=json.dumps(body),
        accept="application/json",
        contentType="application/json",
    )
    payload = json.loads(response["body"].read())
    embedding = payload.get("embedding")
    if not isinstance(embedding, list):
        raise TypeError("Bedrock Titan embedding response did not include an embedding list.")

    return [float(value) for value in embedding]


def embed_text_with_tei(text: str, settings: Settings, input_type: str) -> list[float]:
    """Generate a BGE embedding through Hugging Face TEI."""
    input_text = text
    if input_type == "query" and settings.tei_query_instruction:
        input_text = settings.tei_query_instruction + text

    response = httpx.post(
        f"{settings.tei_base_url.rstrip('/')}/embed",
        json={"inputs": input_text},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise TypeError("TEI embedding response must be a list.")

    embedding = payload[0] if payload and isinstance(payload[0], list) else payload
    if not isinstance(embedding, list):
        raise TypeError("TEI embedding response did not include an embedding list.")

    return [float(value) for value in embedding]


def bedrock_runtime_client(settings: Settings):
    """Create a Bedrock Runtime client from the local AWS environment."""
    if not settings.aws_region:
        raise ValueError("AWS_REGION is required for Bedrock embedding runs.")

    session_kwargs: dict[str, str] = {"region_name": str(settings.aws_region)}
    if settings.aws_profile:
        session_kwargs["profile_name"] = settings.aws_profile

    session = boto3.Session(**session_kwargs)
    return session.client("bedrock-runtime")


def embedding_model_id(settings: Settings) -> str:
    """Return the configured embedding model ID."""
    provider = settings.embedding_provider.lower()
    if provider == "bedrock":
        return settings.bedrock_embedding_model_id or EMBEDDING_MODEL
    if provider == "tei":
        return settings.tei_model_id or BGE_EMBEDDING_MODEL

    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def embedding_dimensions(settings: Settings) -> int:
    """Return and validate the configured embedding dimensions."""
    provider = settings.embedding_provider.lower()
    if provider == "bedrock":
        dimensions = int(settings.bedrock_embedding_dimensions)
    elif provider == "tei":
        dimensions = int(settings.tei_embedding_dimensions)
    else:
        raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")

    if dimensions not in {EMBEDDING_DIMENSIONS, BGE_EMBEDDING_DIMENSIONS}:
        raise ValueError("Memoe currently stores embeddings in VECTOR(256) or VECTOR(384).")
    return dimensions


def vector_column(dimensions: int) -> str:
    """Return the vector column used by the configured dimensions."""
    if dimensions == EMBEDDING_DIMENSIONS:
        return "embedding"
    if dimensions == BGE_EMBEDDING_DIMENSIONS:
        return "embedding_384"

    raise ValueError("Unsupported embedding dimensions.")


def excluded_vector_value(column_name: str, dimensions: int) -> str:
    """Return the ON CONFLICT update value for a vector column."""
    if column_name == vector_column(dimensions):
        return f"excluded.{column_name}"
    return "NULL"


def reset_memory_embedding_index(settings: Settings | None = None) -> None:
    """Drop the derived embedding index so it can be recreated with current dimensions."""
    with connect(settings) as connection:
        connection.execute("DROP TABLE IF EXISTS memory_embeddings")


def vector_literal(embedding: list[float]) -> str:
    """Format an embedding as a CockroachDB VECTOR literal."""
    return "[" + ",".join(str(value) for value in embedding) + "]"


def lifecycle_score(status: str | None) -> float:
    """Score memory lifecycle state."""
    return {
        "validated": 0.2,
        "fresh": 0.12,
        "needs_recheck": -0.05,
        "weakened": -0.18,
        "stale": -0.25,
        "superseded": -0.35,
    }.get(str(status), 0.0)


def evidence_quality_score(rating: str | None) -> float:
    """Score evidence quality metadata."""
    return {
        "strong": 0.15,
        "moderate": 0.08,
        "limited": -0.05,
        "insufficient": -0.15,
        "validation": 0.1,
    }.get(str(rating), 0.0)


def keyword_score(goal: str, text: str) -> float:
    """Reward exact token overlap for operational terms."""
    goal_tokens = set(TOKEN_PATTERN.findall(goal.lower()))
    text_tokens = set(TOKEN_PATTERN.findall(text.lower()))
    if not goal_tokens:
        return 0.0
    overlap = len(goal_tokens & text_tokens) / len(goal_tokens)
    return min(overlap * 0.2, 0.2)


def recency_score(created_at: str | None) -> float:
    """Give a small boost to recent memory."""
    if not created_at:
        return 0.0
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)

    age_days = max((datetime.now(UTC) - created).days, 0)
    if age_days <= 7:
        return 0.08
    if age_days <= 30:
        return 0.04
    return 0.0
