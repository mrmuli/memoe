"""Tests for reflection persistence helpers."""

from __future__ import annotations

from memoe.services.reflection_runner import reflection_signature


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
