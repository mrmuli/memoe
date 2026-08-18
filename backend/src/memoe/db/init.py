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
        deduplicate_observation_evidence(connection)


def deduplicate_observation_evidence(connection) -> None:
    """Keep one evidence link per observation, event, and role."""
    connection.execute(
        """
        DELETE FROM observation_evidence
        WHERE id IN (
          SELECT id
          FROM (
            SELECT
              id,
              row_number() OVER (
                PARTITION BY observation_id, event_id, role
                ORDER BY created_at DESC, id DESC
              ) AS duplicate_rank
            FROM observation_evidence
            WHERE observation_id IS NOT NULL
          )
          WHERE duplicate_rank > 1
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS observation_evidence_observation_event_role_idx
          ON observation_evidence (observation_id, event_id, role)
        """
    )
