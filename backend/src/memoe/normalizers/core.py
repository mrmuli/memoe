"""Normalize source-specific rows into Memoe evidence events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NormalizedEvent:
    """Common event shape used by Memoe after reading source rows."""

    service_slug: str
    component: str
    environment: str
    source_table: str
    source_id: str
    source_type: str
    category: str
    event_type: str
    occurred_at: datetime
    severity: str | None
    summary: str
    external_reference: str | None
    correlation_identifiers: dict[str, Any]
    metadata: dict[str, Any]


def parse_timestamp(value: str) -> datetime:
    """Parse fixture timestamps into timezone-aware datetimes."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def memoe_identity(row: dict[str, Any]) -> dict[str, str]:
    """Return demo identity metadata embedded in fixture rows."""
    identity = row.get("_memoe")
    if not isinstance(identity, dict):
        raise ValueError("Fixture row is missing required _memoe identity metadata.")

    required_keys = ("service_slug", "component", "environment")
    missing = [key for key in required_keys if not identity.get(key)]
    if missing:
        raise ValueError(f"Fixture row _memoe metadata is missing: {', '.join(missing)}")

    return {
        "service_slug": str(identity["service_slug"]),
        "component": str(identity["component"]),
        "environment": str(identity["environment"]),
    }


def normalize_cloudwatch_alarm(row: dict[str, Any]) -> NormalizedEvent:
    """Normalize an aws_cloudwatch_alarm row."""
    identity = memoe_identity(row)
    state_value = str(row.get("state_value", ""))

    return NormalizedEvent(
        service_slug=identity["service_slug"],
        component=identity["component"],
        environment=identity["environment"],
        source_table="aws_cloudwatch_alarm",
        source_id=str(row["arn"]),
        source_type="cloudwatch_alarm",
        category="signal",
        event_type="slo_degradation",
        occurred_at=parse_timestamp(str(row["_memoe_occurred_at"])),
        severity="high" if state_value == "ALARM" else None,
        summary=str(row["state_reason"]),
        external_reference=str(row["arn"]),
        correlation_identifiers={
            "alarm_name": row.get("name"),
            "dimensions": row.get("dimensions", []),
        },
        metadata=row,
    )


def normalize_github_repository_deployment(row: dict[str, Any]) -> NormalizedEvent:
    """Normalize a github_repository_deployment row."""
    identity = memoe_identity(row)

    return NormalizedEvent(
        service_slug=identity["service_slug"],
        component=identity["component"],
        environment=identity["environment"],
        source_table="github_repository_deployment",
        source_id=str(row["id"]),
        source_type="github_repository",
        category="event",
        event_type="deployment_succeeded",
        occurred_at=parse_timestamp(str(row["created_at"])),
        severity=None,
        summary=str(row["description"]),
        external_reference=str(row["repository_full_name"]),
        correlation_identifiers={
            "commit_sha": row.get("commit_sha"),
            "repository_full_name": row.get("repository_full_name"),
        },
        metadata=row,
    )


def normalize_github_pull_request(row: dict[str, Any]) -> NormalizedEvent:
    """Normalize a github_pull_request row."""
    identity = memoe_identity(row)

    return NormalizedEvent(
        service_slug=identity["service_slug"],
        component=identity["component"],
        environment=identity["environment"],
        source_table="github_pull_request",
        source_id=str(row["id"]),
        source_type="github_repository",
        category="event",
        event_type="pull_request_merged",
        occurred_at=parse_timestamp(str(row["merged_at"])),
        severity=None,
        summary=str(row["title"]),
        external_reference=str(row.get("permalink") or row.get("url") or row["number"]),
        correlation_identifiers={
            "head_ref_oid": row.get("head_ref_oid"),
            "repository_full_name": row.get("repository_full_name"),
            "pull_request_number": row.get("number"),
        },
        metadata=row,
    )


def normalize_jira_issue(row: dict[str, Any]) -> NormalizedEvent:
    """Normalize a jira_issue row."""
    identity = memoe_identity(row)

    return NormalizedEvent(
        service_slug=identity["service_slug"],
        component=identity["component"],
        environment=identity["environment"],
        source_table="jira_issue",
        source_id=str(row["id"]),
        source_type="jira_project",
        category="outcome",
        event_type="production_ticket_opened",
        occurred_at=parse_timestamp(str(row["created"])),
        severity=str(row["priority"]) if row.get("priority") else None,
        summary=str(row["summary"]),
        external_reference=str(row["key"]),
        correlation_identifiers={
            "project_key": row.get("project_key"),
            "labels": row.get("labels", []),
        },
        metadata=row,
    )


NORMALIZERS = {
    "aws_cloudwatch_alarm": normalize_cloudwatch_alarm,
    "github_repository_deployment": normalize_github_repository_deployment,
    "github_pull_request": normalize_github_pull_request,
    "jira_issue": normalize_jira_issue,
}


def normalize_row(source_table: str, row: dict[str, Any]) -> NormalizedEvent:
    """Normalize a fixture row by source table name."""
    try:
        normalizer = NORMALIZERS[source_table]
    except KeyError as error:
        raise ValueError(f"No normalizer registered for source table: {source_table}") from error

    return normalizer(row)

