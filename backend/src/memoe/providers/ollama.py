"""Ollama Cloud observation provider."""

from __future__ import annotations

import httpx

from memoe.config import Settings
from memoe.providers.observations import (
    ObservationProvider,
    ObservationRequest,
    ObservationResult,
    build_system_prompt,
    build_user_prompt,
    parse_json_content,
)


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
        parsed = parse_json_content(content, "Ollama")

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
