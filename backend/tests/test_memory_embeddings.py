"""Tests for memory embedding scoring helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memoe.config import Settings
from memoe.services.memory_embeddings import (
    BGE_EMBEDDING_DIMENSIONS,
    EMBEDDING_DIMENSIONS,
    embedding_dimensions,
    keyword_score,
    score_memory_row,
    vector_column,
    vector_literal,
)


def test_score_memory_row_boosts_matching_service_and_quality() -> None:
    """Hybrid scoring should favor scoped, fresh, high-quality memory."""
    recent = datetime.now(UTC) - timedelta(days=1)
    row = {
        "memory_type": "observation",
        "memory_id": "obs-1",
        "vector_similarity": 0.5,
        "embedded_text": "payments checkout latency burn rate alarm gateway timeout",
        "metadata": {
            "service_slug": "payments",
            "lifecycle_status": "fresh",
            "evidence_quality_rating": "strong",
            "created_at": recent.isoformat(),
        },
    }

    result = score_memory_row(
        row,
        goal="payments checkout latency",
        service_scope="payments",
    )

    assert result.service_slug == "payments"
    assert result.vector_similarity == 0.5
    assert result.hybrid_score > 1.0


def test_score_memory_row_penalizes_non_matching_service_scope() -> None:
    """A scoped search should not over-rank another service with equal vector similarity."""
    non_matching_row = {
        "memory_type": "observation",
        "memory_id": "obs-2",
        "vector_similarity": 0.7,
        "embedded_text": "payments checkout latency",
        "metadata": {
            "service_slug": "notifications",
            "lifecycle_status": "fresh",
            "evidence_quality_rating": "moderate",
            "created_at": None,
        },
    }
    matching_row = {
        **non_matching_row,
        "metadata": {
            **non_matching_row["metadata"],
            "service_slug": "payments",
        },
    }

    non_matching_result = score_memory_row(
        non_matching_row,
        goal="payments checkout latency",
        service_scope="payments",
    )
    matching_result = score_memory_row(
        matching_row,
        goal="payments checkout latency",
        service_scope="payments",
    )

    assert non_matching_result.service_slug == "notifications"
    assert matching_result.service_slug == "payments"
    assert matching_result.hybrid_score > non_matching_result.hybrid_score


def test_keyword_score_rewards_exact_operational_token_overlap() -> None:
    """Keyword overlap gives hybrid search a deterministic lexical signal."""
    score = keyword_score(
        goal="checkout latency burn rate",
        text="payments checkout latency burn rate alarm",
    )

    assert score == 0.2


def test_vector_helpers_select_supported_dimensions() -> None:
    """Configured embedding dimensions map to CockroachDB vector columns."""
    bedrock_settings = Settings(embedding_provider="bedrock", bedrock_embedding_dimensions=256)
    tei_settings = Settings(embedding_provider="tei", tei_embedding_dimensions=384)

    assert embedding_dimensions(bedrock_settings) == EMBEDDING_DIMENSIONS
    assert embedding_dimensions(tei_settings) == BGE_EMBEDDING_DIMENSIONS
    assert vector_column(256) == "embedding"
    assert vector_column(384) == "embedding_384"
    assert vector_literal([0.1, -0.2, 3.0]) == "[0.1,-0.2,3.0]"
