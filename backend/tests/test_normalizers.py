"""Tests for fixture normalization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memoe.normalizers.core import normalize_row

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "payments"


def load_fixture(name: str) -> list[dict]:
    """Load one fixture file."""
    return json.loads((FIXTURE_ROOT / name).read_text())


def test_cloudwatch_alarm_normalizes_slo_degradation() -> None:
    """CloudWatch alarm rows become signal evidence with service identity."""
    row = load_fixture("aws_cloudwatch_alarm.json")[0]

    event = normalize_row("aws_cloudwatch_alarm", row)

    assert event.service_slug == "payments"
    assert event.component == "checkout-latency-slo"
    assert event.category == "signal"
    assert event.event_type == "slo_degradation"
    assert event.severity == "high"
    assert event.occurred_at.isoformat() == "2026-08-08T10:11:00+00:00"
    assert event.correlation_identifiers["alarm_name"] == "payments-latency-burn-rate-high"


def test_cloudwatch_log_event_preserves_trace_context() -> None:
    """Telemetry rows keep trace/span context for temporal evidence expansion."""
    row = load_fixture("aws_cloudwatch_log_event.json")[1]

    event = normalize_row("aws_cloudwatch_log_event", row)

    assert event.service_slug == "payments"
    assert event.category == "telemetry"
    assert event.event_type == "checkout_gateway_timeout"
    assert event.severity == "error"
    assert event.occurred_at.isoformat() == "2026-08-08T10:10:18+00:00"
    assert event.correlation_identifiers["trace_id"] == "trace-pay-20260808-101018"
    assert event.correlation_identifiers["http_response_status_code"] == 504


def test_jira_issue_normalizes_customer_outcome() -> None:
    """Jira issue rows become customer outcome evidence."""
    row = load_fixture("jira_issue.json")[0]

    event = normalize_row("jira_issue", row)

    assert event.service_slug == "payments"
    assert event.category == "outcome"
    assert event.event_type == "production_ticket_opened"
    assert event.severity == "High"
    assert event.external_reference == "PAY-243"
    assert event.occurred_at.isoformat() == "2026-08-08T10:27:00+00:00"


def test_missing_memoe_identity_is_rejected() -> None:
    """Demo fixtures must declare service identity explicitly."""
    row = {"arn": "arn:test", "state_value": "ALARM", "state_reason": "bad", "_memoe_occurred_at": "2026-08-08T10:11:00+00:00"}

    with pytest.raises(TypeError, match="missing required _memoe"):
        normalize_row("aws_cloudwatch_alarm", row)
