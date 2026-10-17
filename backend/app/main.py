"""HTTP layer: routes, request validation, and error formatting.

Routes are thin: validate input -> open ONE transaction -> call items_service.
All rules (permissions, workflow, concurrency) live in the service layer.
"""
import logging
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, idempotency, items_service as svc, permissions
from .errors import ApiError
from .security import CurrentUser, create_token, current_user, verify_password

log = logging.getLogger("api")

app = FastAPI(title="OpsDesk API", version="1.0")

ItemType = Literal["incident", "customer_issue", "payment", "compliance", "task"]
Priority = Literal["low", "medium", "high", "urgent"]
Status = Literal["open", "in_progress", "awaiting_approval", "resolved", "closed"]


# ---------------------------------------------------------------------------
# Error handling: every error has the same JSON shape {"error": {...}}
# ---------------------------------------------------------------------------

@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content=jsonable_encoder(exc.to_body()))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={
        "error": {"code": "validation_error", "message": "Some fields are invalid", "details": details}})


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse(status_code=500, content={
        "error": {"code": "internal_error", "message": "Something went wrong. Please try again."}})


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class LoginIn(BaseModel):
    email: str
    password: str


class ItemCreate(BaseModel):
    team_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    type: ItemType
    priority: Priority
    due_date: date | None = None


class ItemUpdate(BaseModel):
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    type: ItemType | None = None
    priority: Priority | None = None
    due_date: date | None = None


class AssignIn(BaseModel):
    assignee_id: int
    version: int


class TransitionIn(BaseModel):
    to_status: Status
    version: int
    note: str | None = Field(default=None, max_length=2000)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


# ---------------------------------------------------------------------------
# Auth & profile
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    with db.transaction() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    with db.transaction() as conn:
        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = %s", (body.email.lower().strip(),)
        ).fetchone()
    if not user or not verify_password(body.password, user["password_hash"]):
        raise ApiError(401, "invalid_credentials", "Wrong email or password")
    return {"token": create_token(user["id"])}


@app.get("/api/me")
def me(user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        teams = conn.execute(
            "SELECT t.id, t.name, m.role FROM memberships m JOIN teams t ON t.id = m.team_id "
            "WHERE m.user_id = %s ORDER BY t.name", (user.id,)
        ).fetchall()
    return {"id": user.id, "email": user.email, "name": user.name, "teams": teams}


@app.get("/api/teams/{team_id}/members")
def team_members(team_id: int, user: CurrentUser = Depends(current_user)):
    permissions.require_team_access(user, team_id, "viewer")
    with db.transaction() as conn:
        return conn.execute(
            "SELECT u.id, u.name, m.role FROM memberships m JOIN users u ON u.id = m.user_id "
            "WHERE m.team_id = %s ORDER BY u.name", (team_id,)
        ).fetchall()


# ---------------------------------------------------------------------------
# Work items
# ---------------------------------------------------------------------------

@app.get("/api/items")
def list_items(view: str | None = None, status: Status | None = None, priority: Priority | None = None,
               type: ItemType | None = None, team_id: int | None = None, assignee: str | None = None,
               q: str | None = None, cursor: str | None = None, limit: int = 25,
               user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.list_items(conn, user, view=view, status=status, priority=priority, type=type,
                              team_id=team_id, assignee=assignee, q=q, cursor=cursor, limit=limit)


@app.get("/api/dashboard")
def dashboard(user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.dashboard_counts(conn, user)


@app.post("/api/items", status_code=201)
def create_item(body: ItemCreate, user: CurrentUser = Depends(current_user),
                idempotency_key: str | None = Header(default=None)):
    data = body.model_dump()
    with db.transaction() as conn:
        if idempotency_key is not None:
            stored = idempotency.begin(conn, user.id, idempotency_key, idempotency.request_hash(data))
            if stored is not None:
                return JSONResponse(status_code=stored["status_code"], content=stored["response"],
                                    headers={"Idempotent-Replayed": "true"})
        item = jsonable_encoder(svc.create_item(conn, user, data))
        if idempotency_key is not None:
            idempotency.finish(conn, user.id, idempotency_key, 201, item)
    return JSONResponse(status_code=201, content=item)


@app.get("/api/items/{item_id}")
def get_item(item_id: int, user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.get_item(conn, user, item_id)


@app.patch("/api/items/{item_id}")
def update_item(item_id: int, body: ItemUpdate, user: CurrentUser = Depends(current_user)):
    changes = body.model_dump(exclude_unset=True)
    version = changes.pop("version")
    # Only due_date may be cleared with null; null for the other fields means "no change".
    changes = {k: v for k, v in changes.items() if v is not None or k == "due_date"}
    with db.transaction() as conn:
        return svc.update_item(conn, user, item_id, version, changes)


@app.post("/api/items/{item_id}/claim")
def claim_item(item_id: int, user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.claim_item(conn, user, item_id)


@app.post("/api/items/{item_id}/release")
def release_item(item_id: int, user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.release_item(conn, user, item_id)


@app.post("/api/items/{item_id}/assign")
def assign_item(item_id: int, body: AssignIn, user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.assign_item(conn, user, item_id, body.assignee_id, body.version)


@app.post("/api/items/{item_id}/transition")
def transition_item(item_id: int, body: TransitionIn, user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.transition_item(conn, user, item_id, body.to_status, body.version, body.note)


@app.get("/api/items/{item_id}/activity")
def item_activity(item_id: int, before_id: int | None = None, limit: int = 50,
                  user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.list_activity(conn, user, item_id, before_id, limit)


@app.post("/api/items/{item_id}/comments", status_code=201)
def add_comment(item_id: int, body: CommentIn, user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        return svc.add_comment(conn, user, item_id, body.body)


# ---------------------------------------------------------------------------
# Notifications (written by the background worker)
# ---------------------------------------------------------------------------

@app.get("/api/notifications")
def notifications(user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT id, item_id, message, read_at, created_at FROM notifications "
            "WHERE user_id = %s ORDER BY id DESC LIMIT 30", (user.id,)
        ).fetchall()
        unread = conn.execute(
            "SELECT count(*) AS n FROM notifications WHERE user_id = %s AND read_at IS NULL", (user.id,)
        ).fetchone()["n"]
    return {"notifications": rows, "unread": unread}


@app.post("/api/notifications/read-all")
def read_all_notifications(user: CurrentUser = Depends(current_user)):
    with db.transaction() as conn:
        conn.execute("UPDATE notifications SET read_at = now() WHERE user_id = %s AND read_at IS NULL", (user.id,))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Serve the built React app (production / Docker). In development, Vite serves it.
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
