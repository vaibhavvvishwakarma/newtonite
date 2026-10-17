"""Database access: one connection pool, and a helper that gives a connection
inside a single transaction.

Every request handler does all its work inside ONE transaction:
  - the change to the work item,
  - the activity event describing it,
  - any outbox job it triggers,
  - the stored idempotency response.
Either all of them are saved, or none are. That is what keeps history and
data from ever disagreeing.
"""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pool: ConnectionPool | None = None


def init_pool(url: str | None = None) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            url or config.DATABASE_URL,
            min_size=1,
            max_size=config.DB_POOL_MAX,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def transaction():
    """Yield a connection with an open transaction.

    Commits when the block finishes normally; rolls back if an exception
    (including our ApiError) escapes the block.
    """
    pool = init_pool()
    with pool.connection() as conn:
        with conn.transaction():
            yield conn
