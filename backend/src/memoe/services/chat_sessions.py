"""Persistence for Memoe chat sessions and working memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memoe.config import Settings
from memoe.db.connection import connect


@dataclass(frozen=True)
class ChatMessageRow:
    """A persisted chat message."""

    id: str
    role: str
    content: str
    retrieved_memory: list[dict[str, Any]]
    reflection_id: str | None
    created_at: str


@dataclass(frozen=True)
class ChatSessionRow:
    """A persisted chat session with working memory."""

    id: str
    title: str
    service_scope: str | None
    status: str
    working_memory: dict[str, Any]
    created_at: str
    updated_at: str
    messages: list[ChatMessageRow]


def create_session(
    message: str | None = None,
    service_scope: str | None = None,
    settings: Settings | None = None,
) -> ChatSessionRow:
    """Create an active chat session."""
    title = session_title(message)
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            INSERT INTO chat_sessions (title, service_scope, working_memory)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (title, service_scope, Jsonb(initial_working_memory(service_scope))),
        )
        session_id = str(cursor.fetchone()["id"])
    return get_session(session_id, resolved_settings)


def get_or_create_latest_session(settings: Settings | None = None) -> ChatSessionRow:
    """Return the latest active chat session, or create one."""
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id
            FROM chat_sessions
            WHERE status = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()

    if row:
        return get_session(str(row["id"]), resolved_settings)
    return create_session(settings=resolved_settings)


def get_session(session_id: str, settings: Settings | None = None) -> ChatSessionRow:
    """Load a chat session and its messages."""
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
              id,
              title,
              service_scope,
              status,
              working_memory,
              created_at,
              updated_at
            FROM chat_sessions
            WHERE id = %s
            """,
            (session_id,),
        )
        session = cursor.fetchone()
        if not session:
            raise ValueError(f"Chat session not found: {session_id}")

        cursor.execute(
            """
            SELECT
              id,
              role,
              content,
              retrieved_memory,
              reflection_id,
              created_at
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        messages = [message_from_row(row) for row in cursor.fetchall()]

    return ChatSessionRow(
        id=str(session["id"]),
        title=str(session["title"]),
        service_scope=session["service_scope"],
        status=str(session["status"]),
        working_memory=dict(session["working_memory"]),
        created_at=str(session["created_at"]),
        updated_at=str(session["updated_at"]),
        messages=messages,
    )


def save_chat_message(
    session_id: str,
    role: str,
    content: str,
    retrieved_memory: list[dict[str, Any]] | None = None,
    reflection_id: str | None = None,
    settings: Settings | None = None,
) -> ChatMessageRow:
    """Persist one chat message."""
    resolved_settings = settings or Settings()
    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            INSERT INTO chat_messages (
              session_id,
              role,
              content,
              retrieved_memory,
              reflection_id
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, role, content, retrieved_memory, reflection_id, created_at
            """,
            (
                session_id,
                role,
                content,
                Jsonb(retrieved_memory or []),
                reflection_id,
            ),
        )
        row = cursor.fetchone()

    return message_from_row(row)


def update_working_memory_after_turn(
    session_id: str,
    user_message: str,
    service_scope: str | None,
    answer: str,
    retrieved_memory: list[dict[str, Any]],
    reflection_id: str | None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Update the current investigation state after a chat turn."""
    resolved_settings = settings or Settings()
    active_services = sorted(
        {
            str(row["service_slug"])
            for row in retrieved_memory
            if row.get("service_slug")
        }
    )
    memory_ids = [
        {
            "type": row.get("memory_type"),
            "id": row.get("memory_id"),
            "service": row.get("service_slug"),
        }
        for row in retrieved_memory[:6]
    ]

    with connect(resolved_settings) as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT working_memory FROM chat_sessions WHERE id = %s",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Chat session not found: {session_id}")

        previous = dict(row["working_memory"])
        turn_count = int(previous.get("turn_count", 0)) + 1
        working_memory = {
            **previous,
            "turn_count": turn_count,
            "current_goal": user_message,
            "service_scope": service_scope,
            "active_services": active_services,
            "last_answer_preview": answer[:500],
            "last_retrieved_memory": memory_ids,
            "last_reflection_id": reflection_id,
        }

        cursor.execute(
            """
            UPDATE chat_sessions
            SET
              title = CASE
                WHEN title = 'New investigation' THEN %s
                ELSE title
              END,
              service_scope = %s,
              working_memory = %s,
              updated_at = now()
            WHERE id = %s
            """,
            (session_title(user_message), service_scope, Jsonb(working_memory), session_id),
        )

    return working_memory


def initial_working_memory(service_scope: str | None) -> dict[str, Any]:
    """Build initial working memory for a new session."""
    return {
        "turn_count": 0,
        "service_scope": service_scope,
        "active_services": [],
        "current_goal": None,
        "last_retrieved_memory": [],
        "last_reflection_id": None,
    }


def session_title(message: str | None) -> str:
    """Create a readable deterministic session title."""
    if not message:
        return "New investigation"
    compact = " ".join(message.split())
    return compact[:80]


def message_from_row(row: dict[str, Any]) -> ChatMessageRow:
    """Convert a database row into a chat message dataclass."""
    return ChatMessageRow(
        id=str(row["id"]),
        role=str(row["role"]),
        content=str(row["content"]),
        retrieved_memory=list(row["retrieved_memory"] or []),
        reflection_id=str(row["reflection_id"]) if row.get("reflection_id") else None,
        created_at=str(row["created_at"]),
    )
