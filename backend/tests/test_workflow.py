"""Workflow rules and activity history."""
from conftest import query


def move(ctx, who, item, to_status):
    return ctx.call(who, "POST", f"/api/items/{item['id']}/transition",
                    json={"to_status": to_status, "version": item["version"]})


def claimed_in_progress(ctx, who="member"):
    item = ctx.create_item()
    item = ctx.call(who, "POST", f"/api/items/{item['id']}/claim").json()
    r = move(ctx, who, item, "in_progress")
    assert r.status_code == 200, r.text
    return r.json()


def test_cannot_skip_steps_open_to_closed(ctx):
    item = ctx.create_item()
    r = move(ctx, "lead", item, "closed")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_transition"
    assert query("SELECT status FROM work_items WHERE id = %s", item["id"])[0][0] == "open"


def test_cannot_start_work_without_an_owner(ctx):
    item = ctx.create_item()
    r = move(ctx, "lead", item, "in_progress")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "needs_assignee"


def test_member_cannot_approve_but_lead_can(ctx):
    item = claimed_in_progress(ctx)
    item = move(ctx, "member", item, "awaiting_approval").json()
    assert item["status"] == "awaiting_approval"

    r = move(ctx, "member", item, "resolved")  # the assignee tries to approve their own work
    assert r.status_code == 403

    r = move(ctx, "lead", item, "resolved")
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"


def test_member_cannot_move_someone_elses_item(ctx):
    item = claimed_in_progress(ctx, who="member")
    r = move(ctx, "member2", item, "awaiting_approval")
    assert r.status_code == 403


def test_closed_item_is_final(ctx):
    item = claimed_in_progress(ctx)
    for who, to in [("member", "awaiting_approval"), ("lead", "resolved"), ("lead", "closed")]:
        item = move(ctx, who, item, to).json()
    assert item["status"] == "closed"
    assert item["allowed_transitions"] == []
    r = ctx.call("lead", "PATCH", f"/api/items/{item['id']}", json={"version": item["version"], "title": "x"})
    assert r.status_code == 409


def test_every_change_is_recorded_in_history(ctx):
    item = claimed_in_progress(ctx)
    item = ctx.call("member", "PATCH", f"/api/items/{item['id']}",
                    json={"version": item["version"], "priority": "urgent"}).json()
    ctx.call("member", "POST", f"/api/items/{item['id']}/comments", json={"body": "Looking into it"})
    move(ctx, "member", item, "awaiting_approval")

    events = ctx.call("viewer", "GET", f"/api/items/{item['id']}/activity").json()["events"]
    kinds = [e["kind"] for e in reversed(events)]
    assert kinds == ["created", "claimed", "status_changed", "updated", "commented", "status_changed"]
    update = next(e for e in events if e["kind"] == "updated")
    assert update["data"]["changes"]["priority"] == ["high", "urgent"]


def test_rejected_change_writes_no_history(ctx):
    item = ctx.create_item()
    before = query("SELECT count(*) FROM activity_events")[0][0]
    move(ctx, "lead", item, "closed")  # rejected
    assert query("SELECT count(*) FROM activity_events")[0][0] == before
