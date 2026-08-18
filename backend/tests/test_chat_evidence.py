"""Tests for chat evidence shaping."""

from __future__ import annotations

from datetime import UTC, datetime

from memoe.services.chat_evidence import evidence_event_from_row


def test_evidence_event_from_row_keeps_temporal_and_reference_fields() -> None:
    """Raw evidence sent to chat should preserve timestamps and external references."""
    row = {
        "id": "event-1",
        "service_slug": "payments",
        "source_table": "jira_issue",
        "category": "outcome",
        "event_type": "production_ticket_opened",
        "occurred_at": datetime(2026, 8, 8, 10, 27, tzinfo=UTC),
        "component": "checkout",
        "severity": "High",
        "summary": "Customers report checkout timeouts",
        "external_reference": "PAY-243",
        "correlation_identifiers": {"project_key": "PAY", "labels": ["customer-impact"]},
        "metadata": {
            "key": "PAY-243",
            "priority": "High",
            "status": "Investigating",
            "description": "Customers report checkout timeouts after payment confirmation.",
            "secret_unused_field": "not included",
        },
        "role": "supporting",
    }

    event = evidence_event_from_row(row)

    assert event == {
        "id": "event-1",
        "service_slug": "payments",
        "source_table": "jira_issue",
        "category": "outcome",
        "event_type": "production_ticket_opened",
        "occurred_at": "2026-08-08T10:27:00+00:00",
        "component": "checkout",
        "severity": "High",
        "summary": "Customers report checkout timeouts",
        "external_reference": "PAY-243",
        "correlation_identifiers": {"project_key": "PAY", "labels": ["customer-impact"]},
        "source_details": {
            "key": "PAY-243",
            "priority": "High",
            "status": "Investigating",
            "description": "Customers report checkout timeouts after payment confirmation.",
        },
        "evidence_role": "supporting",
    }
