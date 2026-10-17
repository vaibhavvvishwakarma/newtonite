"""Apply schema.sql. Safe to run many times and from several containers at once
(an advisory lock makes concurrent runs wait for each other)."""
from pathlib import Path

import psycopg

from . import config

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def migrate(url: str | None = None) -> None:
    with psycopg.connect(url or config.DATABASE_URL, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(424242)")
        try:
            conn.execute(SCHEMA)
        finally:
            conn.execute("SELECT pg_advisory_unlock(424242)")


if __name__ == "__main__":
    migrate()
    print("Schema is up to date.")
