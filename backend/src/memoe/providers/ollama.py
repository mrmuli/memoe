"""Ollama Cloud observation provider."""

from __future__ import annotations

import json

import httpx

from memoe.config import Settings
from memoe.providers.observations import ObservationProvider, ObservationRequest, ObservationResult


class OllamaObservationProvider(ObservationProvider):
    """Generate observations with Ollama Cloud's native chat API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

        if not self.settings.ollama_api_key:
            raise ValueError("OLLAMA_API_KEY is required for Ollama Cloud observation runs.")
        if not self.settings.ollama_base_url:
            raise ValueError("OLLAMA_BASE_URL is required for Ollama observation runs.")
        if not self.settings.ollama_model:
            raise ValueError("OLLAMA_MODEL is required for Ollama observation runs.")

    def generate_observation(self, request: ObservationRequest) -> ObservationResult:
        """Generate a structured operational observation."""
        raw_output = self._chat(request)
        content = raw_output["message"]["content"]
        parsed = parse_json_content(content)

        return ObservationResult(
            statement=str(parsed["statement"]),
            observation_type=str(parsed["observation_type"]),
            confidence=float(parsed["confidence"]),
            evidence_quality=dict(parsed["evidence_quality"]),
            supporting_evidence_ids=[str(value) for value in parsed["supporting_evidence_ids"]],
            rejected_evidence_ids=[str(value) for value in parsed["rejected_evidence_ids"]],
            limitations=[str(value) for value in parsed["limitations"]],
            reasoning_summary=str(parsed["reasoning_summary"]),
            raw_output=raw_output,
            model_id=str(self.settings.ollama_model),
        )

    def _chat(self, request: ObservationRequest) -> dict:
        """Call Ollama's native chat endpoint."""
        base_url = str(self.settings.ollama_base_url).rstrip("/")
        api_key = self.settings.ollama_api_key.get_secret_value()
        payload = {
            "model": self.settings.ollama_model,
            "stream": False,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": build_system_prompt(request),
                },
                {
                    "role": "user",
                    "content": build_user_prompt(request),
                },
            ],
        }

        with httpx.Client(timeout=90) as client:
            response = client.post(
                f"{base_url}/api/chat",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()


def build_system_prompt(request: ObservationRequest) -> str:
    """Build the stable procedural instruction for the model."""
    return f"""You are Memoe.

You generate evidence-backed operational observations for SREs.

Use this stored procedure:

{request.procedure_instructions}

Return only valid JSON matching this schema:

{json.dumps(request.output_schema, indent=2)}
"""


def build_user_prompt(request: ObservationRequest) -> str:
    """Build the evidence payload prompt."""
    return f"""Generate one operational observation from this evidence bundle.

Procedure name: {request.procedure_name}
Procedure version: {request.procedure_version}

Evidence:
{json.dumps(request.evidence, indent=2, default=str)}
"""


def parse_json_content(content: str) -> dict:
    """Parse a model response that should contain a single JSON object."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"Ollama returned non-JSON content: {content}") from error

    if not isinstance(parsed, dict):
        raise ValueError("Ollama response JSON must be an object.")

    validate_observation_payload(parsed)
    return parsed


def validate_observation_payload(payload: dict) -> None:
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
        raise ValueError(f"Ollama response is missing required fields: {', '.join(missing)}")

    confidence = float(payload["confidence"])
    if confidence < 0 or confidence > 1:
        raise ValueError("Ollama response confidence must be between 0 and 1.")

    for field in ("supporting_evidence_ids", "rejected_evidence_ids", "limitations"):
        if not isinstance(payload[field], list):
            raise ValueError(f"Ollama response field must be a list: {field}")

    if not isinstance(payload["evidence_quality"], dict):
        raise ValueError("Ollama response field must be an object: evidence_quality")
