"""Observation provider interfaces."""

from __future__ import annotations

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


class ObservationProvider(Protocol):
    """Generates evidence-backed operational observations."""

    def generate_observation(self, request: ObservationRequest) -> ObservationResult:
        """Generate a structured operational observation."""
