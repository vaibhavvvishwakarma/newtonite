"""Background worker: runs jobs from the outbox table.

Run with:  python -m app.worker

How one job is processed (all in a single transaction):
  1. Pick the oldest due job with  FOR UPDATE SKIP LOCKED.
     -> the row is locked, so two workers never run the same job at the same time;
        SKIP LOCKED means a second worker just takes the next job instead of waiting.
  2. Run the handler inside a SAVEPOINT.
  3. Success  -> status = 'done'.
     Failure  -> attempts + 1, schedule a retry with exponential backoff
                 (2, 4, 8, 16... seconds). After max_attempts -> status = 'failed'
                 and the error is kept in last_error for someone to look at.

If the worker crashes in the middle, the transaction is rolled back, the lock
is released, and the job is simply picked up again later.
"""
import logging
import time

from . import db
from .outbox import HANDLERS

log = logging.getLogger("worker")


def backoff_seconds(attempts: int) -> int:
    return min(2 ** attempts, 300)


def process_one() -> bool:
    """Process a single due job. Returns False if there was nothing to do."""
    with db.transaction() as conn:
        job = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'pending' AND run_after <= now()
            ORDER BY run_after, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if job is None:
            return False

        handler = HANDLERS.get(job["kind"])
        try:
            if handler is None:
                raise RuntimeError(f"No handler for job kind '{job['kind']}'")
            with conn.transaction():  # savepoint: handler failure undoes only the handler's work
                handler(conn, job)
        except Exception as exc:  # noqa: BLE001 - any failure means "retry later"
            attempts = job["attempts"] + 1
            final = attempts >= job["max_attempts"]
            conn.execute(
                """
                UPDATE jobs
                SET attempts = %s,
                    status = %s,
                    last_error = %s,
                    run_after = now() + make_interval(secs => %s),
                    updated_at = now()
                WHERE id = %s
                """,
                (attempts, "failed" if final else "pending", repr(exc)[:1000], backoff_seconds(attempts), job["id"]),
            )
            log.warning("job %s (%s) failed attempt %s: %r%s", job["id"], job["kind"], attempts, exc,
                        " - giving up" if final else "")
            return True

        conn.execute(
            "UPDATE jobs SET status = 'done', attempts = attempts + 1, updated_at = now() WHERE id = %s",
            (job["id"],),
        )
        log.info("job %s (%s) done", job["id"], job["kind"])
        return True


def run_forever(poll_interval: float = 1.0) -> None:
    from .migrate import migrate

    migrate()
    log.info("worker started")
    while True:
        try:
            if not process_one():
                time.sleep(poll_interval)
        except Exception:  # database down etc. - log, wait, try again
            log.exception("worker loop error; retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run_forever()
