"""Idempotency: repeating a create request must not create duplicates."""
from conftest import query
from test_concurrency import run_at_once

BODY = {"title": "Customer charged twice", "type": "payment", "priority": "urgent"}


def body(ctx):
    return {**BODY, "team_id": ctx.team["eng"]}


def count_items():
    return query("SELECT count(*) FROM work_items")[0][0]


def test_same_key_twice_creates_one_item_and_returns_same_response(ctx):
    headers = {"Idempotency-Key": "key-123"}
    r1 = ctx.call("member", "POST", "/api/items", json=body(ctx), headers=headers)
    r2 = ctx.call("member", "POST", "/api/items", json=body(ctx), headers=headers)

    assert r1.status_code == r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.headers.get("Idempotent-Replayed") == "true"
    assert count_items() == 1


def test_same_key_sent_concurrently_creates_one_item(ctx):
    ctx.headers("member")
    headers = {"Idempotency-Key": "double-click"}
    responses = run_at_once([
        (lambda: ctx.call("member", "POST", "/api/items", json=body(ctx), headers=headers)) for _ in range(5)
    ])
    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert count_items() == 1


def test_reusing_key_for_a_different_request_is_rejected(ctx):
    headers = {"Idempotency-Key": "key-abc"}
    ctx.call("member", "POST", "/api/items", json=body(ctx), headers=headers)
    r = ctx.call("member", "POST", "/api/items", json={**body(ctx), "title": "Something else"}, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "idempotency_key_reused"
    assert count_items() == 1


def test_keys_are_per_user(ctx):
    headers = {"Idempotency-Key": "same-key"}
    ctx.call("member", "POST", "/api/items", json=body(ctx), headers=headers)
    r = ctx.call("member2", "POST", "/api/items", json=body(ctx), headers=headers)
    assert r.status_code == 201
    assert count_items() == 2


def test_failed_request_does_not_burn_the_key(ctx):
    """A request rejected by the server leaves no stored key, so it can be fixed and retried."""
    headers = {"Idempotency-Key": "retry-me"}
    r1 = ctx.call("viewer", "POST", "/api/items", json=body(ctx), headers=headers)
    assert r1.status_code == 403
    assert query("SELECT count(*) FROM idempotency_keys")[0][0] == 0
