"""Database connection helpers."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection

from memoe.config import Settings


def normalize_psycopg_url(database_url: str) -> str:
    """Normalize SQLAlchemy-style URLs into URLs psycopg can open directly."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def connect(settings: Settings | None = None) -> Iterator[Connection]:
    """Open a psycopg connection using runtime settings."""
    resolved_settings = settings or Settings()
    database_url = normalize_psycopg_url(resolved_settings.database_url)

    with psycopg.connect(database_url) as connection:
        yield connection
