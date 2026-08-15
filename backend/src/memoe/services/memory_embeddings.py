"""Embed and search Memoe memory with CockroachDB vectors."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect

EMBEDDING_DIMENSIONS = 64
EMBEDDING_MODEL = "memoe-hash-v1"
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
    embedded = 0
    counts = {"observation": 0, "reflection": 0, "validation_result": 0}

    with connect(settings) as connection:
        rows = fetch_memory_rows(connection)
        for row in rows:
            embedding = embed_text(row["embedded_text"])
            upsert_memory_embedding(
                connection=connection,
                memory_type=row["memory_type"],
                memory_id=row["memory_id"],
                embedded_text=row["embedded_text"],
                embedding=embedding,
                metadata=row["metadata"],
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
) -> None:
    """Store one memory embedding."""
    connection.execute(
        """
        INSERT INTO memory_embeddings (
          memory_type,
          memory_id,
          embedding_model,
          embedding,
          embedded_text,
          metadata
        )
        VALUES (%s, %s, %s, %s::VECTOR(64), %s, %s)
        ON CONFLICT (memory_type, memory_id, embedding_model)
        DO UPDATE SET
          embedding = excluded.embedding,
          embedded_text = excluded.embedded_text,
          metadata = excluded.metadata,
          updated_at = now()
        """,
        (
            memory_type,
            memory_id,
            EMBEDDING_MODEL,
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
    query_embedding = vector_literal(embed_text(goal))
    candidate_limit = max(limit * 4, 20)

    with connect(settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              memory_type,
              memory_id,
              embedded_text,
              metadata,
              1 - (embedding <=> %s::VECTOR(64)) AS vector_similarity
            FROM memory_embeddings
            WHERE embedding_model = %s
            ORDER BY embedding <=> %s::VECTOR(64)
            LIMIT %s
            """,
            (query_embedding, EMBEDDING_MODEL, query_embedding, candidate_limit),
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


def embed_text(text: str) -> list[float]:
    """Create a deterministic local text embedding for vector-search plumbing."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    tokens = TOKEN_PATTERN.findall(text.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector

    return [round(value / norm, 6) for value in vector]


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
