"""Background jobs: transactional outbox, retries, and idempotent handlers."""
import pytest

from app import outbox, worker
from conftest import query


def assign(ctx, item, to="member"):
    return ctx.call("lead", "POST", f"/api/items/{item['id']}/assign",
                    json={"assignee_id": ctx.user[to], "version": item["version"]})


def make_due():
    """Pretend the retry delay has passed."""
    query_exec("UPDATE jobs SET run_after = now() WHERE status = 'pending'")


def query_exec(sql, *params):
    import psycopg

    from conftest import URL
    with psycopg.connect(URL) as conn:
        conn.execute(sql, params)


def test_job_is_written_with_the_change_and_creates_a_notification(ctx):
    item = ctx.create_item()
    assert assign(ctx, item).status_code == 200
    assert query("SELECT kind, status FROM jobs") == [("notify_assignee", "pending")]

    assert worker.process_one() is True
    assert worker.process_one() is False  # nothing left

    notes = ctx.call("member", "GET", "/api/notifications").json()
    assert notes["unread"] == 1
    assert "assigned you" in notes["notifications"][0]["message"]


def test_no_job_when_the_change_is_rejected(ctx):
    item = ctx.create_item()
    r = ctx.call("lead", "POST", f"/api/items/{item['id']}/assign",
                 json={"assignee_id": ctx.user["member"], "version": 999})  # stale version
    assert r.status_code == 409
    assert query("SELECT count(*) FROM jobs")[0][0] == 0


def test_running_the_same_job_twice_creates_one_notification(ctx):
    item = ctx.create_item()
    assign(ctx, item)
    worker.process_one()
    # Simulate "the worker did the work but crashed before marking the job done":
    query_exec("UPDATE jobs SET status = 'pending'")
    worker.process_one()

    assert query("SELECT count(*) FROM notifications")[0][0] == 1


def test_failing_job_is_retried_then_marked_failed(ctx, monkeypatch):
    calls = []

    def broken_handler(conn, job):
        calls.append(job["id"])
        raise ConnectionError("email provider down")

    monkeypatch.setitem(outbox.HANDLERS, "notify_assignee", broken_handler)
    item = ctx.create_item()
    assign(ctx, item)

    worker.process_one()
    (status, attempts, error, delayed) = query(
        "SELECT status, attempts, last_error, run_after > now() FROM jobs")[0]
    assert (status, attempts, delayed) == ("pending", 1, True)  # retry scheduled for later
    assert "email provider down" in error
    assert worker.process_one() is False  # not due yet (backoff)

    for _ in range(4):
        make_due()
        worker.process_one()

    assert query("SELECT status, attempts FROM jobs")[0] == ("failed", 5)
    assert len(calls) == 5
    make_due()
    assert worker.process_one() is False  # failed jobs are not retried forever


@pytest.mark.parametrize("attempts,expected", [(1, 2), (3, 8), (20, 300)])
def test_backoff_grows_and_is_capped(attempts, expected):
    assert worker.backoff_seconds(attempts) == expected
