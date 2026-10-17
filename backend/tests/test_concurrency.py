"""Concurrency: several users acting on the same item at the same moment."""
import threading

from conftest import query


def run_at_once(fns):
    """Start all functions at the same instant (a barrier) and collect their results."""
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def runner(i, fn):
        barrier.wait()
        results[i] = fn()

    threads = [threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_claim_only_one_wins(ctx):
    item = ctx.create_item(who="lead")
    racers = [f"racer{i}" for i in range(6)] + ["member", "member2"]
    for who in racers:
        ctx.headers(who)  # log in beforehand so the race is only about the claim

    responses = run_at_once([
        (lambda who=who: ctx.call(who, "POST", f"/api/items/{item['id']}/claim")) for who in racers
    ])

    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 1, codes
    assert codes.count(409) == len(racers) - 1
    assert all(r.json()["error"]["code"] == "already_claimed" for r in responses if r.status_code == 409)

    winner = next(r.json() for r in responses if r.status_code == 200)
    (row,) = query("SELECT assignee_id, version FROM work_items WHERE id = %s", item["id"])
    assert row[0] == winner["assignee_id"]
    assert row[1] == 2  # changed exactly once
    claims = query("SELECT count(*) FROM activity_events WHERE item_id = %s AND kind = 'claimed'", item["id"])
    assert claims[0][0] == 1  # history agrees with the data


def test_stale_update_is_rejected_and_data_unchanged(ctx):
    item = ctx.create_item()
    v1 = item["version"]

    # member edits first, based on version 1 -> OK, version becomes 2
    r1 = ctx.call("member", "PATCH", f"/api/items/{item['id']}", json={"version": v1, "title": "Server down (EU)"})
    assert r1.status_code == 200
    assert r1.json()["version"] == v1 + 1

    # member2 still has version 1 open in their browser and changes priority
    r2 = ctx.call("member2", "PATCH", f"/api/items/{item['id']}", json={"version": v1, "priority": "low"})
    assert r2.status_code == 409
    err = r2.json()["error"]
    assert err["code"] == "version_conflict"
    assert err["current"]["title"] == "Server down (EU)"  # client gets the latest copy to show
    assert err["current"]["version"] == v1 + 1

    (row,) = query("SELECT priority, title FROM work_items WHERE id = %s", item["id"])
    assert row == ("high", "Server down (EU)")  # stale change was NOT applied


def test_simultaneous_edits_with_same_version_only_one_applies(ctx):
    item = ctx.create_item()
    editors = ["member", "member2", "lead", "racer0", "racer1"]
    for who in editors:
        ctx.headers(who)

    responses = run_at_once([
        (lambda who=who: ctx.call(who, "PATCH", f"/api/items/{item['id']}",
                                  json={"version": item["version"], "title": f"edited by {who}"}))
        for who in editors
    ])
    codes = [r.status_code for r in responses]
    assert codes.count(200) == 1, codes
    assert codes.count(409) == len(editors) - 1

    updates = query("SELECT count(*) FROM activity_events WHERE item_id = %s AND kind = 'updated'", item["id"])
    assert updates[0][0] == 1
