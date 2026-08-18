"""Tests for observation persistence helpers."""

from __future__ import annotations

from memoe.services.observation_runner import observation_signature, summarize_evidence_for_logs


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


def test_summarize_evidence_for_logs_keeps_only_high_level_fields() -> None:
    """Observation logs should describe evidence shape without raw event payloads."""
    summary = summarize_evidence_for_logs(
        [
            {
                "source_table": "jira_issue",
                "category": "outcome",
                "event_type": "customer_report_opened",
                "component": "checkout-api",
                "occurred_at": "2026-08-08T10:27:00+00:00",
                "summary": "Customer cannot complete payment",
                "metadata": {"description": "Full ticket text"},
            },
            {
                "source_table": "aws_cloudwatch_alarm",
                "category": "signal",
                "event_type": "slo_burn_rate_alarm",
                "component": "checkout-api",
                "occurred_at": "2026-08-08T10:11:00+00:00",
                "summary": "Latency burn rate high",
                "metadata": {"state_reason": "Raw CloudWatch reason"},
            },
        ]
    )

    assert summary == {
        "total": 2,
        "source_tables": {"jira_issue": 1, "aws_cloudwatch_alarm": 1},
        "categories": {"outcome": 1, "signal": 1},
        "event_types": {"customer_report_opened": 1, "slo_burn_rate_alarm": 1},
        "components": {"checkout-api": 2},
        "time_range": {
            "start": "2026-08-08T10:11:00+00:00",
            "end": "2026-08-08T10:27:00+00:00",
        },
    }
