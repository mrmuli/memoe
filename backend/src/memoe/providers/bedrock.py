"""Amazon Bedrock observation provider."""

from __future__ import annotations

from typing import Any

import boto3

from memoe.config import Settings
from memoe.providers.observations import (
    ObservationProvider,
    ObservationRequest,
    ObservationResult,
    build_system_prompt,
    build_user_prompt,
    parse_json_content,
)


class BedrockObservationProvider(ObservationProvider):
    """Generate observations with Amazon Bedrock's Converse API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

        if not self.settings.aws_region:
            raise ValueError("AWS_REGION is required for Bedrock observation runs.")
        if not self.settings.bedrock_model_id:
            raise ValueError("BEDROCK_MODEL_ID is required for Bedrock observation runs.")

    def generate_observation(self, request: ObservationRequest) -> ObservationResult:
        """Generate a structured operational observation."""
        raw_output = self._converse(request)
        content = response_text(raw_output)
        parsed = parse_json_content(content, "Bedrock")

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
            model_id=str(self.settings.bedrock_model_id),
        )

    def _converse(self, request: ObservationRequest) -> dict[str, Any]:
        """Call Bedrock's model-agnostic Converse endpoint."""
        client = self._client()
        return client.converse(
            modelId=str(self.settings.bedrock_model_id),
            system=[{"text": build_system_prompt(request)}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": build_user_prompt(request)}],
                }
            ],
            inferenceConfig={
                "maxTokens": self.settings.bedrock_max_tokens,
                "temperature": self.settings.bedrock_temperature,
            },
        )

    def _client(self):
        """Create a Bedrock Runtime client from the local AWS environment."""
        session_kwargs: dict[str, str] = {"region_name": str(self.settings.aws_region)}
        if self.settings.aws_profile:
            session_kwargs["profile_name"] = self.settings.aws_profile

        session = boto3.Session(**session_kwargs)
        return session.client("bedrock-runtime")


def response_text(response: dict[str, Any]) -> str:
    """Extract the assistant text content from a Bedrock Converse response."""
    content_blocks = response["output"]["message"]["content"]
    text_blocks = [block["text"] for block in content_blocks if "text" in block]
    if not text_blocks:
        block_types = sorted({key for block in content_blocks for key in block})
        stop_reason = response.get("stopReason", "unknown")
        raise ValueError(
            "Bedrock response did not contain text content. "
            f"stopReason={stop_reason}; content block types={', '.join(block_types) or 'none'}."
        )

    return "\n".join(text_blocks)
