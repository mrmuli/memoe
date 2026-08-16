"""FastAPI app for the Memoe demo UI."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from memoe.config import Settings
from memoe.db.connection import connect
from memoe.services.chat_graph import run_chat_graph
from memoe.services.chat_sessions import (
    create_session,
    get_or_create_latest_session,
    get_session,
    save_chat_message,
    update_working_memory_after_turn,
)
from memoe.services.memory_embeddings import refresh_memory_embeddings
from memoe.services.observation_runner import list_observations, run_observation
from memoe.services.reflection_runner import list_reflections, run_reflection

app = FastAPI(title="Memoe API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    """Request body for a Memoe chat turn."""

    session_id: str | None = None
    message: str = Field(min_length=1)
    service_scope: str | None = None
    limit: int = Field(default=6, ge=1, le=20)
    reflect: bool = False


class ObservationRunRequest(BaseModel):
    """Request body for running an observation."""

    service_slug: str = Field(min_length=1)
    provider: str = "bedrock"


class ReflectionRunRequest(BaseModel):
    """Request body for running a reflection."""

    goal: str | None = None
    service_scope: str | None = None
    provider: str = "bedrock"
    limit: int = Field(default=8, ge=1, le=20)


class ChatSessionCreateRequest(BaseModel):
    """Request body for creating a chat session."""

    message: str | None = None
    service_scope: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    """Return API health."""
    return {"status": "ok"}


@app.get("/services")
def services() -> list[dict[str, Any]]:
    """List known services."""
    settings = Settings()
    with connect(settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              s.slug,
              s.name,
              s.owner,
              s.criticality,
              count(e.id) AS event_count
            FROM services s
            LEFT JOIN events e ON e.service_id = s.id
            GROUP BY s.id, s.slug, s.name, s.owner, s.criticality
            ORDER BY s.slug
            """
        )
        rows = cursor.fetchall()

    return [
        {
            "slug": row["slug"],
            "name": row["name"],
            "owner": row["owner"],
            "criticality": row["criticality"],
            "event_count": int(row["event_count"]),
        }
        for row in rows
    ]


@app.get("/observations")
def observations(service: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """List observations."""
    return [asdict(row) for row in list_observations(service_slug=service, limit=limit)]


@app.get("/reflections")
def reflections(limit: int = 20) -> list[dict[str, Any]]:
    """List reflections."""
    return [asdict(row) for row in list_reflections(limit=limit)]


@app.get("/chat/sessions/latest")
def latest_chat_session() -> dict[str, Any]:
    """Load the latest active chat session."""
    try:
        session = get_or_create_latest_session(Settings())
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return asdict(session)


@app.post("/chat/sessions")
def create_chat_session(request: ChatSessionCreateRequest) -> dict[str, Any]:
    """Create a new chat session."""
    try:
        session = create_session(
            message=request.message,
            service_scope=request.service_scope,
            settings=Settings(),
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return asdict(session)


@app.get("/chat/sessions/{session_id}")
def chat_session(session_id: str) -> dict[str, Any]:
    """Load one chat session."""
    try:
        session = get_session(session_id, Settings())
    except Exception as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return asdict(session)


@app.post("/observations/run")
def run_observation_endpoint(request: ObservationRunRequest) -> dict[str, Any]:
    """Run observation generation for one service."""
    try:
        result = run_observation(request.service_slug, request.provider, Settings())
        refresh_memory_embeddings(Settings())
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return asdict(result)


@app.post("/reflections/run")
def run_reflection_endpoint(request: ReflectionRunRequest) -> dict[str, Any]:
    """Run goal-scoped reflection generation."""
    try:
        result = run_reflection(
            provider_name=request.provider,
            limit=request.limit,
            goal=request.goal,
            service_scope=request.service_scope,
            settings=Settings(),
        )
        refresh_memory_embeddings(Settings())
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return asdict(result)


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    """Ask Memoe a question."""
    settings = Settings()
    try:
        session = (
            get_session(request.session_id, settings)
            if request.session_id
            else create_session(
                message=request.message,
                service_scope=request.service_scope,
                settings=settings,
            )
        )
        save_chat_message(
            session_id=session.id,
            role="user",
            content=request.message,
            settings=settings,
        )
        result = run_chat_graph(
            message=request.message,
            provider="bedrock",
            service_scope=request.service_scope,
            limit=request.limit,
            reflect=request.reflect,
            working_memory=session.working_memory,
            settings=settings,
        )
        reflection_id = result.reflection["reflection_id"] if result.reflection else None
        save_chat_message(
            session_id=session.id,
            role="assistant",
            content=result.answer,
            retrieved_memory=result.retrieved_memory,
            reflection_id=reflection_id,
            settings=settings,
        )
        working_memory = update_working_memory_after_turn(
            session_id=session.id,
            user_message=request.message,
            service_scope=request.service_scope,
            answer=result.answer,
            retrieved_memory=result.retrieved_memory,
            reflection_id=reflection_id,
            settings=settings,
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    payload = asdict(result)
    payload["session_id"] = session.id
    payload["working_memory"] = working_memory
    return payload
