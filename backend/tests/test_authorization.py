"""Authorization is enforced by the server, per team and per item."""


def test_requests_without_a_token_are_rejected(ctx):
    r = ctx.client().get("/api/items")
    assert r.status_code == 401


def test_user_from_another_team_cannot_read_or_change_an_item(ctx):
    item = ctx.create_item()
    path = f"/api/items/{item['id']}"
    assert ctx.call("outsider", "GET", path).status_code == 403
    assert ctx.call("outsider", "GET", path + "/activity").status_code == 403
    assert ctx.call("outsider", "PATCH", path, json={"version": 1, "title": "hacked"}).status_code == 403
    assert ctx.call("outsider", "POST", path + "/claim").status_code == 403
    assert ctx.call("outsider", "POST", path + "/comments", json={"body": "hi"}).status_code == 403
    assert ctx.call("member", "GET", path).json()["title"] == "Server down"  # unchanged


def test_list_only_returns_items_from_my_teams(ctx):
    ctx.create_item()
    assert ctx.call("outsider", "GET", "/api/items").json()["items"] == []
    # asking explicitly for another team's items is refused, not silently filtered
    r = ctx.call("outsider", "GET", f"/api/items?team_id={ctx.team['eng']}")
    assert r.status_code == 403


def test_cannot_create_item_in_a_team_you_are_not_in(ctx):
    r = ctx.call("outsider", "POST", "/api/items",
                 json={"team_id": ctx.team["eng"], "title": "x", "type": "task", "priority": "low"})
    assert r.status_code == 403


def test_viewer_can_read_but_not_write(ctx):
    item = ctx.create_item()
    assert ctx.call("viewer", "GET", f"/api/items/{item['id']}").status_code == 200
    assert ctx.call("viewer", "POST", f"/api/items/{item['id']}/claim").status_code == 403
    r = ctx.call("viewer", "POST", "/api/items",
                 json={"team_id": ctx.team["eng"], "title": "x", "type": "task", "priority": "low"})
    assert r.status_code == 403


def test_only_lead_can_assign_to_others(ctx):
    item = ctx.create_item()
    body = {"assignee_id": ctx.user["member2"], "version": item["version"]}
    assert ctx.call("member", "POST", f"/api/items/{item['id']}/assign", json=body).status_code == 403
    r = ctx.call("lead", "POST", f"/api/items/{item['id']}/assign", json=body)
    assert r.status_code == 200
    assert r.json()["assignee_id"] == ctx.user["member2"]


def test_cannot_assign_to_someone_outside_the_team(ctx):
    item = ctx.create_item()
    r = ctx.call("lead", "POST", f"/api/items/{item['id']}/assign",
                 json={"assignee_id": ctx.user["outsider"], "version": item["version"]})
    assert r.status_code == 422
    r = ctx.call("lead", "POST", f"/api/items/{item['id']}/assign",
                 json={"assignee_id": ctx.user["viewer"], "version": item["version"]})
    assert r.status_code == 422
