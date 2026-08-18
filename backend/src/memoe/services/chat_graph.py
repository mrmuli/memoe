"""LangGraph orchestration for Memoe conversations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

import boto3
from langgraph.graph import END, START, StateGraph

from memoe.config import Settings
from memoe.providers.bedrock import response_text
from memoe.providers.observations import strip_json_code_fence
from memoe.services.chat_evidence import expand_evidence_for_memory
from memoe.services.memory_embeddings import (
    MemorySearchResult,
    refresh_memory_embeddings,
    search_memory,
)
from memoe.services.reflection_runner import ReflectionRunResult, run_reflection


class ChatGraphState(TypedDict, total=False):
    """State passed between Memoe chat graph nodes."""

    message: str
    provider: str
    service_scope: str | None
    limit: int
    reflect: bool
    working_memory: dict[str, Any] | None
    question_route: dict[str, Any]
    retrieved_memory: list[dict[str, Any]]
    evidence_detail: list[dict[str, Any]]
    reflection: dict[str, Any] | None
    answer: str


@dataclass(frozen=True)
class ChatGraphResult:
    """Result returned from a Memoe chat graph run."""

    answer: str
    retrieved_memory: list[dict[str, Any]]
    reflection: dict[str, Any] | None


def run_chat_graph(
    message: str,
    provider: str = "bedrock",
    service_scope: str | None = None,
    limit: int = 8,
    reflect: bool = False,
    working_memory: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> ChatGraphResult:
    """Run a Memoe conversation turn through LangGraph."""
    graph = build_chat_graph(settings or Settings())
    result = graph.invoke(
        {
            "message": message,
            "provider": provider,
            "service_scope": service_scope,
            "limit": limit,
            "reflect": reflect,
            "working_memory": working_memory,
        }
    )
    return ChatGraphResult(
        answer=str(result["answer"]),
        retrieved_memory=list(result.get("retrieved_memory", [])),
        reflection=result.get("reflection"),
    )


def build_chat_graph(settings: Settings):
    """Build the Memoe chat graph."""

    def route_question(state: ChatGraphState) -> dict[str, Any]:
        return {"question_route": generate_question_route_with_bedrock(state, settings)}

    def retrieve_memory(state: ChatGraphState) -> dict[str, Any]:
        results = search_memory(
            goal=state["message"],
            service_scope=state.get("service_scope"),
            limit=int(state.get("limit", 8)),
            settings=settings,
        )
        return {"retrieved_memory": [memory_result_to_dict(row) for row in results]}

    def expand_evidence_detail(state: ChatGraphState) -> dict[str, Any]:
        route = state.get("question_route", {})
        if not route.get("needs_evidence_detail") and route.get("answer_mode") != "ticket_lookup":
            return {"evidence_detail": []}
        evidence = expand_evidence_for_memory(
            retrieved_memory=state.get("retrieved_memory", []),
            answer_mode=route.get("answer_mode"),
            settings=settings,
        )
        return {"evidence_detail": evidence}

    def maybe_reflect(state: ChatGraphState) -> dict[str, Any]:
        reflection = run_reflection(
            provider_name=state.get("provider", "bedrock"),
            limit=int(state.get("limit", 8)),
            goal=state["message"],
            service_scope=state.get("service_scope"),
            settings=settings,
        )
        refresh_memory_embeddings(settings)
        return {"reflection": reflection_result_to_dict(reflection)}

    def answer_with_memory(state: ChatGraphState) -> dict[str, Any]:
        answer = generate_answer_with_bedrock(state, settings)
        return {"answer": answer}

    def route_after_retrieval(state: ChatGraphState) -> Literal["reflect", "answer"]:
        if state.get("reflect"):
            return "reflect"
        return "answer"

    builder = StateGraph(ChatGraphState)
    builder.add_node("route_question", route_question)
    builder.add_node("retrieve_memory", retrieve_memory)
    builder.add_node("expand_evidence_detail", expand_evidence_detail)
    builder.add_node("reflect", maybe_reflect)
    builder.add_node("answer", answer_with_memory)
    builder.add_edge(START, "route_question")
    builder.add_edge("route_question", "retrieve_memory")
    builder.add_edge("retrieve_memory", "expand_evidence_detail")
    builder.add_conditional_edges(
        "expand_evidence_detail",
        route_after_retrieval,
        {"reflect": "reflect", "answer": "answer"},
    )
    builder.add_edge("reflect", "answer")
    builder.add_edge("answer", END)
    return builder.compile()


def generate_question_route_with_bedrock(state: ChatGraphState, settings: Settings) -> dict[str, Any]:
    """Use the model to route a chat question without hardcoded domain keywords."""
    if not settings.aws_region:
        raise ValueError("AWS_REGION is required for Memoe chat routing.")
    if not settings.bedrock_model_id:
        raise ValueError("BEDROCK_MODEL_ID is required for Memoe chat routing.")

    client = bedrock_runtime_client(settings)
    response = client.converse(
        modelId=str(settings.bedrock_model_id),
        system=[{"text": chat_route_system_prompt()}],
        messages=[
            {
                "role": "user",
                "content": [{"text": chat_route_user_prompt(state)}],
            }
        ],
        inferenceConfig={
            "maxTokens": min(settings.bedrock_max_tokens, 700),
            "temperature": 0,
        },
    )
    parsed = parse_json_object(response_text(response), "Bedrock chat router")
    return normalize_question_route(parsed)


def generate_answer_with_bedrock(state: ChatGraphState, settings: Settings) -> str:
    """Answer a user question using retrieved Memoe memory."""
    if not settings.aws_region:
        raise ValueError("AWS_REGION is required for Memoe chat.")
    if not settings.bedrock_model_id:
        raise ValueError("BEDROCK_MODEL_ID is required for Memoe chat.")

    client = bedrock_runtime_client(settings)
    response = client.converse(
        modelId=str(settings.bedrock_model_id),
        system=[{"text": chat_system_prompt()}],
        messages=[
            {
                "role": "user",
                "content": [{"text": chat_user_prompt(state)}],
            }
        ],
        inferenceConfig={
            "maxTokens": settings.bedrock_max_tokens,
            "temperature": settings.bedrock_temperature,
        },
    )
    return normalize_chat_answer(response_text(response))


def chat_system_prompt() -> str:
    """Build the stable Memoe chat instruction."""
    return """You are Memoe, answering in a UI chat panel for SRE intelligence, to SREs and other stakeholders.

Keep the answer scannable and operational.
Use at most 90 words unless the user asks for more detail.
Return plain text only. Do not use Markdown tables. Do not use Markdown bold.
Do not restate every retrieved memory.
Do not invent facts or imply causality beyond the evidence.
Do not say caused or triggered unless the retrieved memory contains conclusive validation. Prefer associated with, followed by, or consistent with.
Treat retrieved observations and reflections as memory claims with evidence quality, not absolute truth.
Use working memory as current conversation state, but prefer retrieved memory for factual claims.
If no service scope is provided, request the service scope first and if none is provided, answer across the most relevant services in retrieved memory.
For broad questions, focus on the top relevant services, up to five, instead of asking the user to choose a service.
Ask for clarification only when the question cannot be answered usefully without a narrower scope.

Adapt the answer shape to the user's question.
If the user asks for a ticket number, Jira issue, or customer incident identifier, answer the ticket key first.
If question_route.answer_mode is not ticket_lookup, do not lead with a ticket number even when Jira evidence is present.
Follow question_route.lead_with when it is supported by retrieved memory or expanded evidence.
If expanded evidence contains an external_reference from jira_issue, treat that as the Jira ticket key only for ticket or customer incident questions.
If the user asks for a timestamp, source, or detailed evidence, answer the relevant direct fact first.
For detailed evidence, use short bullets grouped by source or timeline. Do not use a table.
For risk or pattern summaries, answer in concise natural language and include concrete operational implications.
Use bullets only when they make evidence easier to scan.
Mention confidence or missing evidence when it materially affects the answer.

Prefer:
- concrete service names
- concrete risk or weakness
- next investigative action
- short bullets

Avoid:
- long summaries
- Markdown tables
- Markdown emphasis
- generic reliability advice
- repeating the same evidence in multiple ways"""


def chat_user_prompt(state: ChatGraphState) -> str:
    """Build the user prompt for a Memoe chat turn."""
    payload = {
        "question": state["message"],
        "service_scope": state.get("service_scope"),
        "question_route": state.get("question_route", {}),
        "working_memory": state.get("working_memory"),
        "retrieved_memory": state.get("retrieved_memory", []),
        "evidence_detail": state.get("evidence_detail", []),
        "generated_reflection": state.get("reflection"),
    }
    return f"""Answer the user question using this Memoe context.

Context:
{json.dumps(payload, indent=2, default=str)}
"""


def chat_route_system_prompt() -> str:
    """Build instructions for model-based chat routing."""
    return """You route Memoe chat questions for an SRE intelligence application.

Return only valid JSON.
Do not answer the user.
Infer the user's intent from meaning, not from fixed keywords.

Schema:
{
  "answer_mode": "risk_summary | evidence_detail | ticket_lookup | slo_detail | log_detail | deployment_detail | comparison | clarification | general",
  "focus": ["short focus terms"],
  "needs_evidence_detail": true,
  "lead_with": "the first thing the answer should address",
  "reason": "brief routing reason"
}

Guidance:
- Use ticket_lookup only when the user asks for a ticket, issue, incident identifier, or customer incident reference.
- Use slo_detail when the user asks about SLOs, error budget, burn rates, objectives, alarms, or their measurements.
- Use evidence_detail when the user asks to show proof, source records, timestamps, or supporting details.
- Use log_detail when the user asks about logs or log events.
- Use deployment_detail when the user asks about deploys, changes, commits, PRs, or rollbacks.
- Set needs_evidence_detail true when raw events would materially improve the answer.
- Set clarification only when the question cannot be answered usefully without a missing scope or target."""


def chat_route_user_prompt(state: ChatGraphState) -> str:
    """Build the user prompt for chat routing."""
    payload = {
        "question": state["message"],
        "service_scope": state.get("service_scope"),
        "working_memory": state.get("working_memory"),
    }
    return json.dumps(payload, indent=2, default=str)


def normalize_question_route(route: dict[str, Any]) -> dict[str, Any]:
    """Normalize model routing output to the fields the graph expects."""
    allowed_modes = {
        "risk_summary",
        "evidence_detail",
        "ticket_lookup",
        "slo_detail",
        "log_detail",
        "deployment_detail",
        "comparison",
        "clarification",
        "general",
    }
    answer_mode = str(route.get("answer_mode", "general"))
    if answer_mode not in allowed_modes:
        answer_mode = "general"

    focus = route.get("focus", [])
    if not isinstance(focus, list):
        focus = [str(focus)]

    return {
        "answer_mode": answer_mode,
        "focus": [str(item) for item in focus[:8]],
        "needs_evidence_detail": bool(route.get("needs_evidence_detail", False)),
        "lead_with": str(route.get("lead_with", answer_mode)),
        "reason": str(route.get("reason", "")),
    }


def parse_json_object(content: str, provider_name: str) -> dict[str, Any]:
    """Parse a model response that should contain one JSON object."""
    stripped = strip_json_code_fence(content)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise ValueError(f"{provider_name} returned non-JSON content: {content}") from error

    if not isinstance(parsed, dict):
        raise TypeError(f"{provider_name} response JSON must be an object.")
    return parsed


def normalize_chat_answer(answer: str) -> str:
    """Remove presentation markup that makes chat responses feel templated."""
    clean = answer.replace("**", "").replace("*", "").replace("`", "")
    clean = re.sub(r"\bcaused\b", "was associated with", clean, flags=re.IGNORECASE)
    return re.sub(r"\bcausing\b", "being associated with", clean, flags=re.IGNORECASE)


def bedrock_runtime_client(settings: Settings):
    """Create a Bedrock Runtime client from the local AWS environment."""
    session_kwargs: dict[str, str] = {"region_name": str(settings.aws_region)}
    if settings.aws_profile:
        session_kwargs["profile_name"] = settings.aws_profile

    session = boto3.Session(**session_kwargs)
    return session.client("bedrock-runtime")


def memory_result_to_dict(row: MemorySearchResult) -> dict[str, Any]:
    """Convert a memory search result into graph state."""
    return {
        "memory_type": row.memory_type,
        "memory_id": row.memory_id,
        "hybrid_score": row.hybrid_score,
        "vector_similarity": row.vector_similarity,
        "service_slug": row.service_slug,
        "lifecycle_status": row.lifecycle_status,
        "evidence_quality_rating": row.evidence_quality_rating,
        "created_at": row.created_at,
        "text": row.text,
    }


def reflection_result_to_dict(result: ReflectionRunResult) -> dict[str, Any]:
    """Convert a persisted reflection result into graph state."""
    return {
        "run_id": result.run_id,
        "reflection_id": result.reflection_id,
        "statement": result.statement,
        "confidence": result.confidence,
        "evidence_quality": result.evidence_quality,
        "supporting_observation_ids": result.supporting_observation_ids,
        "rejected_observation_ids": result.rejected_observation_ids,
        "limitations": result.limitations,
        "details": result.details,
    }
