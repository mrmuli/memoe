"""Observation provider interfaces."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ObservationRequest:
    """Input sent to a model-backed observation provider."""

    procedure_name: str
    procedure_version: int
    procedure_instructions: str
    output_schema: dict
    evidence: list[dict]


@dataclass(frozen=True)
class ObservationResult:
    """Structured observation returned by a provider."""

    statement: str
    observation_type: str
    confidence: float
    evidence_quality: dict
    supporting_evidence_ids: list[str]
    rejected_evidence_ids: list[str]
    limitations: list[str]
    reasoning_summary: str
    raw_output: dict
    model_id: str
    extra_fields: dict


class ObservationProvider(Protocol):
    """Generates evidence-backed operational observations."""

    def generate_observation(self, request: ObservationRequest) -> ObservationResult:
        """Generate a structured operational observation."""


def build_system_prompt(request: ObservationRequest) -> str:
    """Build the stable procedural instruction for the model."""
    return f"""You are Memoe.

You generate evidence-backed operational observations for SREs.

Use this stored procedure:

{request.procedure_instructions}

Return only valid JSON matching this schema:

{json.dumps(request.output_schema, indent=2)}

Include every field listed in the schema's required array. Do not omit required fields.
"""


def build_user_prompt(request: ObservationRequest) -> str:
    """Build the evidence payload prompt."""
    return f"""Generate one operational observation from this evidence bundle.

Procedure name: {request.procedure_name}
Procedure version: {request.procedure_version}

Evidence:
{json.dumps(request.evidence, indent=2, default=str)}
"""


def parse_json_content(content: str, provider_name: str) -> dict:
    """Parse a model response that should contain a single JSON object."""
    content = strip_json_code_fence(content)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"{provider_name} returned non-JSON content: {content}") from error

    if not isinstance(parsed, dict):
        raise TypeError(f"{provider_name} response JSON must be an object.")

    validate_observation_payload(parsed, provider_name)
    return parsed


def strip_json_code_fence(content: str) -> str:
    """Return JSON content without an optional Markdown code fence."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()

    return stripped


def validate_observation_payload(payload: dict, provider_name: str) -> None:
    """Validate the minimum observation payload contract."""
    required = {
        "statement",
        "observation_type",
        "confidence",
        "evidence_quality",
        "supporting_evidence_ids",
        "rejected_evidence_ids",
        "limitations",
        "reasoning_summary",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"{provider_name} response is missing required fields: {', '.join(missing)}")

    confidence = float(payload["confidence"])
    if confidence < 0 or confidence > 1:
        raise ValueError(f"{provider_name} response confidence must be between 0 and 1.")

    for field in ("supporting_evidence_ids", "rejected_evidence_ids", "limitations"):
        if not isinstance(payload[field], list):
            raise TypeError(f"{provider_name} response field must be a list: {field}")

    if not isinstance(payload["evidence_quality"], dict):
        raise TypeError(f"{provider_name} response field must be an object: evidence_quality")


def validate_output_schema_required_fields(
    payload: dict,
    output_schema: dict,
    provider_name: str,
) -> None:
    """Validate that the provider included every schema-required field."""
    required = output_schema.get("required", [])
    if not isinstance(required, list):
        return

    missing = sorted(str(field) for field in required if field not in payload)
    if missing:
        raise ValueError(
            f"{provider_name} response is missing output-schema required fields: "
            f"{', '.join(missing)}"
        )
