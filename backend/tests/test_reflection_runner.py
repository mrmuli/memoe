"""Tests for reflection persistence helpers."""

from __future__ import annotations

from memoe.services.reflection_runner import reflection_signature, reflection_title


def test_reflection_signature_uses_sorted_supporting_evidence_ids() -> None:
    """Same reflection evidence should dedupe even if IDs arrive in a different order."""
    first = reflection_signature(
        reflection_type="recovery_gap",
        statement="Payments needs better rollback evidence.",
        supporting_ids=["obs-b", "obs-a"],
    )
    second = reflection_signature(
        reflection_type="recovery_gap",
        statement="Different wording should not matter when evidence is the same.",
        supporting_ids=["obs-a", "obs-b"],
    )

    assert first == second


def test_reflection_signature_falls_back_to_normalized_statement() -> None:
    """Statement fallback should ignore punctuation and case changes."""
    first = reflection_signature(
        reflection_type="detection_gap",
        statement="Trace-level evidence is missing for Payments!",
        supporting_ids=[],
    )
    second = reflection_signature(
        reflection_type="detection_gap",
        statement="trace level evidence is missing for payments",
        supporting_ids=[],
    )

    assert first == second


def test_reflection_signature_keeps_reflection_type_distinct() -> None:
    """Different pattern classes should not collapse into one signature."""
    detection = reflection_signature(
        reflection_type="detection_gap",
        statement="Trace-level evidence is missing for Payments",
        supporting_ids=[],
    )
    recovery = reflection_signature(
        reflection_type="recovery_gap",
        statement="Trace-level evidence is missing for Payments",
        supporting_ids=[],
    )

    assert detection != recovery


def test_reflection_title_names_single_service_scope() -> None:
    """Single-service reflection titles should make the service obvious."""
    assert reflection_title("recurring_pattern", ["orders"]) == "orders service: recurring_pattern"


def test_reflection_title_names_small_cross_service_scope() -> None:
    """Small cross-service reflection titles should list the service slugs."""
    assert (
        reflection_title("dependency_risk", ["inventory", "orders"])
        == "inventory + orders: dependency_risk"
    )


def test_reflection_title_summarizes_large_cross_service_scope() -> None:
    """Large cross-service reflection titles should stay compact."""
    title = reflection_title(
        "detection_gap",
        ["payments", "orders", "inventory", "notifications", "search"],
    )

    assert title == "cross-service (5 services): detection_gap"
