"""Idempotency keys: make "create" safe to retry.

The client generates a random key (UUID) when the user opens the form, and
sends it as the `Idempotency-Key` header. If the same request arrives twice
(double click, timeout + retry), the second one gets the stored response of the
first instead of creating a second item.

How it is safe even when both copies arrive at the SAME moment:
  `INSERT ... ON CONFLICT DO NOTHING` on the (user_id, key) primary key.
  The second transaction's insert BLOCKS until the first transaction commits;
  then it sees the conflict and reads the stored response. If the first one
  failed and rolled back, the key row disappears with it, so the retry runs
  normally.
"""
import hashlib
import json

from psycopg.types.json import Jsonb

from .errors import ApiError


def request_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def begin(conn, user_id: int, key: str, body_hash: str) -> dict | None:
    """Reserve the key. Returns None if this is the first time we see it,
    otherwise returns the stored {status_code, response} to replay."""
    if not key or len(key) > 200:
        raise ApiError(422, "invalid_idempotency_key", "Idempotency-Key must be 1-200 characters")

    inserted = conn.execute(
        """
        INSERT INTO idempotency_keys (user_id, key, request_hash)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, key) DO NOTHING
        RETURNING key
        """,
        (user_id, key, body_hash),
    ).fetchone()
    if inserted:
        return None

    existing = conn.execute(
        "SELECT request_hash, status_code, response FROM idempotency_keys WHERE user_id = %s AND key = %s",
        (user_id, key),
    ).fetchone()
    if existing["request_hash"] != body_hash:
        raise ApiError(422, "idempotency_key_reused",
                       "This Idempotency-Key was already used for a different request")
    return {"status_code": existing["status_code"], "response": existing["response"]}


def finish(conn, user_id: int, key: str, status_code: int, response: dict) -> None:
    conn.execute(
        "UPDATE idempotency_keys SET status_code = %s, response = %s WHERE user_id = %s AND key = %s",
        (status_code, Jsonb(response), user_id, key),
    )
