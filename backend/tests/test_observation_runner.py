"""Tests for observation persistence helpers."""

from __future__ import annotations

from memoe.services.observation_runner import observation_signature


def test_observation_signature_uses_service_type_and_sorted_supporting_evidence() -> None:
    """Same service, type, and evidence should dedupe across wording changes."""
    first = observation_signature(
        service_slug="notifications",
        observation_type="inconclusive",
        statement="Deployment probably did not cause the SLO degradation.",
        supporting_ids=["event-b", "event-a"],
    )
    second = observation_signature(
        service_slug="notifications",
        observation_type="inconclusive",
        statement="Different wording for the same evidence.",
        supporting_ids=["event-a", "event-b"],
    )

    assert first == second


def test_observation_signature_keeps_services_distinct() -> None:
    """The same evidence shape in two services should not collapse together."""
    notifications = observation_signature(
        service_slug="notifications",
        observation_type="inconclusive",
        statement="Deployment probably did not cause the SLO degradation.",
        supporting_ids=["event-a", "event-b"],
    )
    payments = observation_signature(
        service_slug="payments",
        observation_type="inconclusive",
        statement="Deployment probably did not cause the SLO degradation.",
        supporting_ids=["event-a", "event-b"],
    )

    assert notifications != payments


def test_observation_signature_falls_back_to_normalized_statement_without_evidence() -> None:
    """Empty-evidence observations should dedupe only when the statement matches."""
    first = observation_signature(
        service_slug="notifications",
        observation_type="inconclusive",
        statement="No supporting evidence was returned!",
        supporting_ids=[],
    )
    second = observation_signature(
        service_slug="notifications",
        observation_type="inconclusive",
        statement="no supporting evidence was returned",
        supporting_ids=[],
    )

    assert first == second
