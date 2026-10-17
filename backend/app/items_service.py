"""Business logic for work items.

Pattern used by EVERY write operation (read this once, and every function
below makes sense):

    1. lock_item()        SELECT ... FOR UPDATE  -> nobody else can change the
                          row until our transaction ends. Concurrent requests
                          for the same item wait in line.
    2. check permission   permissions.py, scoped to the item's team.
    3. check version      the client's version must equal the current one,
                          otherwise 409 version_conflict (stale data).
    4. check rules        workflow / "already claimed" / etc.
    5. UPDATE the row     version = version + 1, updated_at = now()
    6. record()           append an activity event        } same transaction,
    7. outbox.enqueue()   (if someone must be notified)   } so all-or-nothing

All functions receive `conn`, a connection already inside a transaction
(see db.transaction()).
"""
import base64
from datetime import datetime

from psycopg.types.json import Jsonb

from . import outbox, permissions, workflow
from .errors import ApiError, not_found
from .security import CurrentUser

ITEM_SELECT = """
    SELECT w.id, w.team_id, t.name AS team_name, w.title, w.description, w.type,
           w.priority, w.status, w.assignee_id, a.name AS assignee_name,
           w.created_by, c.name AS created_by_name, w.due_date, w.version,
           w.created_at, w.updated_at
    FROM work_items w
    JOIN teams t      ON t.id = w.team_id
    JOIN users c      ON c.id = w.created_by
    LEFT JOIN users a ON a.id = w.assignee_id
"""

EDITABLE_FIELDS = ("title", "description", "type", "priority", "due_date")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize(row: dict) -> dict:
    item = dict(row)
    item["allowed_transitions"] = workflow.allowed_next(item["status"])
    return item


def fetch_item(conn, item_id: int) -> dict:
    row = conn.execute(ITEM_SELECT + " WHERE w.id = %s", (item_id,)).fetchone()
    if row is None:
        raise not_found()
    return _serialize(row)


def lock_item(conn, item_id: int) -> dict:
    """Lock the row for the rest of this transaction and return its current values."""
    row = conn.execute("SELECT * FROM work_items WHERE id = %s FOR UPDATE", (item_id,)).fetchone()
    if row is None:
        raise not_found()
    return row


def check_version(conn, row: dict, expected_version: int) -> None:
    """Optimistic locking: reject changes based on an out-of-date copy of the item."""
    if row["version"] != expected_version:
        raise ApiError(
            409, "version_conflict",
            "This item was changed by someone else since you loaded it. Reload to see the latest version.",
            current=fetch_item(conn, row["id"]),
        )


def record(conn, item_id: int, actor_id: int, kind: str, data: dict | None = None) -> None:
    """Append to the activity history. Never updated or deleted."""
    conn.execute(
        "INSERT INTO activity_events (item_id, actor_id, kind, data) VALUES (%s, %s, %s, %s)",
        (item_id, actor_id, kind, Jsonb(data or {})),
    )


def _bump(conn, item_id: int, sets: str, params: tuple) -> None:
    conn.execute(
        f"UPDATE work_items SET {sets}, version = version + 1, updated_at = now() WHERE id = %s",
        (*params, item_id),
    )


def _notify_assignee(conn, user: CurrentUser, item_id: int, assignee_id: int, title: str) -> None:
    outbox.enqueue(conn, "notify_assignee", {
        "item_id": item_id, "assignee_id": assignee_id, "actor_id": user.id,
        "actor_name": user.name, "title": title,
    })


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_item(conn, user: CurrentUser, item_id: int) -> dict:
    item = fetch_item(conn, item_id)
    permissions.require_team_access(user, item["team_id"], "viewer")
    return item


def _encode_cursor(ts: datetime, item_id: int) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{item_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        ts, item_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(ts), int(item_id)
    except Exception:
        raise ApiError(422, "invalid_cursor", "Invalid pagination cursor")


def list_items(conn, user: CurrentUser, *, view: str | None = None, status: str | None = None,
               priority: str | None = None, type: str | None = None, team_id: int | None = None,
               assignee: str | None = None, q: str | None = None, cursor: str | None = None,
               limit: int = 25) -> dict:
    """Server-side filtering + keyset (cursor) pagination.

    The WHERE clause ALWAYS restricts to the user's own teams, so a user can
    never list items of a team they don't belong to - whatever filters they send.
    """
    where = ["w.team_id = ANY(%s)"]
    params: list = [user.team_ids]

    if team_id is not None:
        permissions.require_team_access(user, team_id, "viewer")
        where.append("w.team_id = %s")
        params.append(team_id)

    # Pre-built "needs attention" views
    if view == "mine":
        where.append("w.assignee_id = %s AND w.status NOT IN ('resolved', 'closed')")
        params.append(user.id)
    elif view == "unassigned_urgent":
        where.append("w.assignee_id IS NULL AND w.priority IN ('high', 'urgent') "
                     "AND w.status NOT IN ('resolved', 'closed')")
    elif view == "awaiting_my_approval":
        where.append("w.status = 'awaiting_approval' AND w.team_id = ANY(%s)")
        params.append(user.teams_with_role("lead"))
    elif view not in (None, "", "all"):
        raise ApiError(422, "invalid_view", f"Unknown view '{view}'")

    if status:
        where.append("w.status = %s")
        params.append(status)
    if priority:
        where.append("w.priority = %s")
        params.append(priority)
    if type:
        where.append("w.type = %s")
        params.append(type)
    if assignee == "me":
        where.append("w.assignee_id = %s")
        params.append(user.id)
    elif assignee == "unassigned":
        where.append("w.assignee_id IS NULL")
    elif assignee:
        where.append("w.assignee_id = %s")
        params.append(int(assignee))
    if q and q.strip():
        where.append("w.search_vector @@ websearch_to_tsquery('english', %s)")
        params.append(q.strip())

    # Keyset pagination: "give me the rows older than the last one I saw".
    # Unlike OFFSET, this stays fast on page 500 and does not skip/duplicate
    # rows when new items are added while paging.
    if cursor:
        ts, last_id = _decode_cursor(cursor)
        where.append("(w.updated_at, w.id) < (%s, %s)")
        params.extend([ts, last_id])

    limit = max(1, min(limit, 100))
    sql = (ITEM_SELECT + " WHERE " + " AND ".join(where)
           + " ORDER BY w.updated_at DESC, w.id DESC LIMIT %s")
    rows = conn.execute(sql, (*params, limit + 1)).fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor(rows[-1]["updated_at"], rows[-1]["id"]) if has_more else None
    return {"items": [_serialize(r) for r in rows], "next_cursor": next_cursor}


def dashboard_counts(conn, user: CurrentUser) -> dict:
    row = conn.execute(
        """
        SELECT
          count(*) FILTER (WHERE assignee_id = %(me)s AND status NOT IN ('resolved','closed')) AS mine,
          count(*) FILTER (WHERE assignee_id IS NULL AND priority IN ('high','urgent')
                           AND status NOT IN ('resolved','closed')) AS unassigned_urgent,
          count(*) FILTER (WHERE status = 'awaiting_approval' AND team_id = ANY(%(lead)s)) AS awaiting_my_approval
        FROM work_items
        WHERE team_id = ANY(%(teams)s)
        """,
        {"me": user.id, "lead": user.teams_with_role("lead"), "teams": user.team_ids},
    ).fetchone()
    return dict(row)


def list_activity(conn, user: CurrentUser, item_id: int, before_id: int | None = None, limit: int = 50) -> dict:
    get_item(conn, user, item_id)  # permission check
    limit = max(1, min(limit, 100))
    rows = conn.execute(
        """
        SELECT e.id, e.kind, e.data, e.created_at, e.actor_id, u.name AS actor_name
        FROM activity_events e JOIN users u ON u.id = e.actor_id
        WHERE e.item_id = %s AND (%s::bigint IS NULL OR e.id < %s)
        ORDER BY e.id DESC
        LIMIT %s
        """,
        (item_id, before_id, before_id, limit + 1),
    ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"events": rows, "next_before_id": rows[-1]["id"] if has_more else None}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create_item(conn, user: CurrentUser, data: dict) -> dict:
    permissions.require_team_access(user, data["team_id"], "member")
    row = conn.execute(
        """
        INSERT INTO work_items (team_id, title, description, type, priority, due_date, created_by)
        VALUES (%(team_id)s, %(title)s, %(description)s, %(type)s, %(priority)s, %(due_date)s, %(created_by)s)
        RETURNING id
        """,
        {**data, "created_by": user.id},
    ).fetchone()
    record(conn, row["id"], user.id, "created", {"title": data["title"], "priority": data["priority"]})
    return fetch_item(conn, row["id"])


def update_item(conn, user: CurrentUser, item_id: int, version: int, changes: dict) -> dict:
    row = lock_item(conn, item_id)
    permissions.require_team_access(user, row["team_id"], "member")
    check_version(conn, row, version)
    if row["status"] == "closed":
        raise ApiError(409, "item_closed", "Closed items cannot be edited")

    diff = {f: [row[f], v] for f, v in changes.items() if f in EDITABLE_FIELDS and row[f] != v}
    if not diff:
        return fetch_item(conn, item_id)

    sets = ", ".join(f"{f} = %s" for f in diff)
    _bump(conn, item_id, sets, tuple(new for _, new in diff.values()))
    record(conn, item_id, user.id, "updated", {"changes": {f: [str(o) if o is not None else None,
                                                                str(n) if n is not None else None]
                                                            for f, (o, n) in diff.items()}})
    return fetch_item(conn, item_id)


def claim_item(conn, user: CurrentUser, item_id: int) -> dict:
    """Take ownership of an unassigned item.

    Two users clicking "Claim" at the same moment: the first request locks the
    row and sets the assignee; the second waits for the lock, then sees the
    item is already assigned and gets 409 already_claimed. Exactly one wins.
    """
    row = lock_item(conn, item_id)
    permissions.require_team_access(user, row["team_id"], "member")
    if row["status"] == "closed":
        raise ApiError(409, "item_closed", "Closed items cannot be claimed")
    if row["assignee_id"] is not None:
        if row["assignee_id"] == user.id:
            return fetch_item(conn, item_id)  # already mine: treat a repeat click as success
        current = fetch_item(conn, item_id)
        raise ApiError(409, "already_claimed", f"Already claimed by {current['assignee_name']}", current=current)

    _bump(conn, item_id, "assignee_id = %s", (user.id,))
    record(conn, item_id, user.id, "claimed", {"assignee_id": user.id, "assignee_name": user.name})
    return fetch_item(conn, item_id)


def release_item(conn, user: CurrentUser, item_id: int) -> dict:
    row = lock_item(conn, item_id)
    permissions.require_owner_or_lead(user, row, "release")
    if row["assignee_id"] is None:
        return fetch_item(conn, item_id)
    if row["status"] in ("awaiting_approval", "resolved", "closed"):
        raise ApiError(409, "cannot_release", f"Items in status '{row['status']}' cannot be released")
    # An unowned item cannot be "in progress", so it goes back to open.
    _bump(conn, item_id, "assignee_id = NULL, status = 'open'", ())
    record(conn, item_id, user.id, "released", {"previous_assignee_id": row["assignee_id"],
                                                 "previous_status": row["status"]})
    return fetch_item(conn, item_id)


def assign_item(conn, user: CurrentUser, item_id: int, assignee_id: int, version: int) -> dict:
    row = lock_item(conn, item_id)
    permissions.require_team_access(user, row["team_id"], "member")
    if not permissions.is_lead(user, row["team_id"]):
        raise ApiError(403, "forbidden", "Only a team lead can assign items to other people")
    check_version(conn, row, version)
    if row["status"] == "closed":
        raise ApiError(409, "item_closed", "Closed items cannot be reassigned")

    target = conn.execute(
        "SELECT u.id, u.name, m.role FROM users u "
        "LEFT JOIN memberships m ON m.user_id = u.id AND m.team_id = %s WHERE u.id = %s",
        (row["team_id"], assignee_id),
    ).fetchone()
    permissions.require_assignable(target["role"] if target else None)
    if row["assignee_id"] == assignee_id:
        return fetch_item(conn, item_id)

    _bump(conn, item_id, "assignee_id = %s", (assignee_id,))
    record(conn, item_id, user.id, "assigned", {"from_assignee_id": row["assignee_id"],
                                                 "to_assignee_id": assignee_id, "to_assignee_name": target["name"]})
    _notify_assignee(conn, user, item_id, assignee_id, row["title"])
    return fetch_item(conn, item_id)


def transition_item(conn, user: CurrentUser, item_id: int, to_status: str, version: int,
                    note: str | None = None) -> dict:
    row = lock_item(conn, item_id)
    permissions.require_owner_or_lead(user, row, "change the status of")
    check_version(conn, row, version)
    workflow.check_transition(row, to_status, permissions.is_lead(user, row["team_id"]))

    _bump(conn, item_id, "status = %s", (to_status,))
    record(conn, item_id, user.id, "status_changed", {"from": row["status"], "to": to_status, "note": note})
    if to_status == "awaiting_approval":
        outbox.enqueue(conn, "notify_leads", {"item_id": item_id, "team_id": row["team_id"],
                                              "actor_id": user.id, "title": row["title"]})
    return fetch_item(conn, item_id)


def add_comment(conn, user: CurrentUser, item_id: int, body: str) -> dict:
    row = lock_item(conn, item_id)
    permissions.require_team_access(user, row["team_id"], "member")
    record(conn, item_id, user.id, "commented", {"body": body})
    # Commenting does not change the item, so it does not bump `version`
    # (a comment should not cause someone else's edit to conflict).
    conn.execute("UPDATE work_items SET updated_at = now() WHERE id = %s", (item_id,))
    return fetch_item(conn, item_id)
