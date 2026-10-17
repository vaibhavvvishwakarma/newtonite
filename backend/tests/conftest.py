"""Test setup.

Tests run against a REAL PostgreSQL database (not SQLite, not mocks), because
the behaviours we care about - row locks, unique constraints, transactions -
only exist in the real database.

Uses TEST_DATABASE_URL (default: a local `opsdesk_test` database).
The database is wiped before every test.
"""
import os

os.environ.setdefault("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/opsdesk_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["BCRYPT_ROUNDS"] = "4"  # fast hashing in tests

import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.migrate import migrate  # noqa: E402
from app.security import hash_password  # noqa: E402

URL = os.environ["TEST_DATABASE_URL"]
PASSWORD = "pw"

# name -> {team: role}
USERS = {
    "lead":     {"eng": "lead"},
    "member":   {"eng": "member"},
    "member2":  {"eng": "member"},
    "viewer":   {"eng": "viewer"},
    "outsider": {"fin": "member"},
    **{f"racer{i}": {"eng": "member"} for i in range(6)},  # extra members for concurrency tests
}


@pytest.fixture(scope="session", autouse=True)
def schema():
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(URL)
    db.init_pool(URL)
    yield
    db.close_pool()


class Ctx:
    """Holds ids created by the fixture and helpers to call the API as a given user."""

    def __init__(self, team_ids, user_ids):
        self.team = team_ids
        self.user = user_ids
        self._tokens = {}

    def client(self) -> TestClient:
        return TestClient(app, raise_server_exceptions=False)

    def headers(self, who: str) -> dict:
        if who not in self._tokens:
            r = self.client().post("/api/auth/login", json={"email": f"{who}@test.dev", "password": PASSWORD})
            assert r.status_code == 200, r.text
            self._tokens[who] = r.json()["token"]
        return {"Authorization": f"Bearer {self._tokens[who]}"}

    def call(self, who: str, method: str, path: str, **kw):
        headers = {**self.headers(who), **kw.pop("headers", {})}
        return self.client().request(method, path, headers=headers, **kw)

    def create_item(self, who="member", **fields):
        body = {"team_id": self.team["eng"], "title": "Server down", "type": "incident", "priority": "high",
                **fields}
        r = self.call(who, "POST", "/api/items", json=body)
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture
def ctx():
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("TRUNCATE users, teams, memberships, work_items, activity_events, idempotency_keys, "
                     "jobs, notifications RESTART IDENTITY CASCADE")
        team_ids = {name: conn.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (name,)).fetchone()[0]
                    for name in ("eng", "fin")}
        pw = hash_password(PASSWORD)
        user_ids = {}
        for name, roles in USERS.items():
            uid = conn.execute("INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id",
                               (f"{name}@test.dev", name.title(), pw)).fetchone()[0]
            user_ids[name] = uid
            for team, role in roles.items():
                conn.execute("INSERT INTO memberships VALUES (%s, %s, %s)", (uid, team_ids[team], role))
    return Ctx(team_ids, user_ids)


def query(sql: str, *params):
    with psycopg.connect(URL) as conn:
        return conn.execute(sql, params).fetchall()
