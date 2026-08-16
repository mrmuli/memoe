"""Database initialization."""

from pathlib import Path

from memoe.config import Settings
from memoe.db.connection import connect

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def initialize_database(settings: Settings | None = None) -> None:
    """Create the Memoe database schema if it does not already exist."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = [statement.strip() for statement in schema_sql.split(";") if statement.strip()]

    with connect(settings) as connection:
        for statement in statements:
            connection.execute(statement)
