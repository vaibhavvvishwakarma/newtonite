"""Transactional outbox + the job handlers the worker runs.

Why an outbox?
  If we sent a notification directly inside the request and the database
  transaction then failed, we would have notified about something that never
  happened. Instead we INSERT a job row in the same transaction as the change.
  If the transaction commits, the job exists; if it rolls back, the job is gone
  too. A separate worker process (app/worker.py) runs the jobs later.

Delivery guarantee: at-least-once. A job can run more than once (e.g. the
worker crashes after doing the work but before marking it done), so every
handler must be IDEMPOTENT. Ours insert notifications with
ON CONFLICT (job_id, user_id) DO NOTHING, so a second run changes nothing.
"""
from psycopg.types.json import Jsonb


def enqueue(conn, kind: str, payload: dict) -> None:
    conn.execute("INSERT INTO jobs (kind, payload) VALUES (%s, %s)", (kind, Jsonb(payload)))


# ---------------------------------------------------------------------------
# Handlers. Each receives (conn, job) and runs inside a savepoint: if it raises,
# its partial work is rolled back and the job is retried later.
# ---------------------------------------------------------------------------

def _notify(conn, job: dict, user_id: int, message: str) -> None:
    conn.execute(
        """
        INSERT INTO notifications (user_id, item_id, job_id, message)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (job_id, user_id) DO NOTHING
        """,
        (user_id, job["payload"]["item_id"], job["id"], message),
    )


def handle_notify_assignee(conn, job: dict) -> None:
    p = job["payload"]
    if p["assignee_id"] == p["actor_id"]:
        return  # no need to notify someone about their own action
    _notify(conn, job, p["assignee_id"], f'{p["actor_name"]} assigned you #{p["item_id"]}: {p["title"]}')


def handle_notify_leads(conn, job: dict) -> None:
    p = job["payload"]
    leads = conn.execute(
        "SELECT user_id FROM memberships WHERE team_id = %s AND role = 'lead'", (p["team_id"],)
    ).fetchall()
    for lead in leads:
        if lead["user_id"] != p["actor_id"]:
            _notify(conn, job, lead["user_id"], f'#{p["item_id"]} is waiting for your approval: {p["title"]}')


HANDLERS = {
    "notify_assignee": handle_notify_assignee,
    "notify_leads": handle_notify_leads,
}
