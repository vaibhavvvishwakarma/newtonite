"""Authentication: password hashing, tokens, and the `current_user` dependency."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Header

from . import config, db
from .errors import ApiError


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=config.TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


@dataclass
class CurrentUser:
    id: int
    email: str
    name: str
    # team_id -> role ('viewer' | 'member' | 'lead')
    roles: dict[int, str] = field(default_factory=dict)

    @property
    def team_ids(self) -> list[int]:
        return list(self.roles.keys())

    def teams_with_role(self, *roles: str) -> list[int]:
        return [team_id for team_id, role in self.roles.items() if role in roles]


def current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    """FastAPI dependency. Reads `Authorization: Bearer <token>`.

    Team roles are loaded from the database on EVERY request (not stored in the
    token), so removing someone from a team takes effect immediately.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthenticated", "Please log in")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise ApiError(401, "unauthenticated", "Session expired, please log in again")

    with db.transaction() as conn:
        user = conn.execute(
            "SELECT id, email, name FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if not user:
            raise ApiError(401, "unauthenticated", "User no longer exists")
        rows = conn.execute(
            "SELECT team_id, role FROM memberships WHERE user_id = %s", (user_id,)
        ).fetchall()

    return CurrentUser(
        id=user["id"],
        email=user["email"],
        name=user["name"],
        roles={r["team_id"]: r["role"] for r in rows},
    )
