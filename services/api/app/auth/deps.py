"""Authentication, CSRF and workspace-scoped authorization dependencies.

Every workspace route depends on `ws_member` (any role) or `require_role(...)`. A user who is not a member of the
workspace receives 404 so record existence is not disclosed across tenants.
"""
import hashlib
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.time import utcnow
from app.models import AuthSession, Membership, User

COOKIE_NAME = "techpm_session"
ROLE_RANK = {"member": 1, "manager": 2, "admin": 3}
_hasher = PasswordHasher()


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, pw)
    except (VerifyMismatchError, Exception):  # noqa: BLE001
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> tuple[str, AuthSession]:
    raw = secrets.token_urlsafe(32)
    s = AuthSession(
        token_hash=_hash_token(raw),
        csrf_token=secrets.token_urlsafe(24),
        user_id=user.id,
        expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours),
    )
    db.add(s)
    db.flush()
    return raw, s


class RateLimiter:
    """In-process sliding window. Adequate for a single API instance; use Redis for multi-instance deployments."""

    def __init__(self) -> None:
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        st = get_settings()
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > st.login_window_seconds:
            q.popleft()
        if len(q) >= st.login_max_attempts:
            raise HTTPException(429, "Too many attempts. Try again later.")
        q.append(now)

    def reset(self) -> None:
        self.hits.clear()


auth_limiter = RateLimiter()


def current_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(401, "Not authenticated")
    s = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash_token(raw)))
    if not s or s.expires_at < utcnow():
        raise HTTPException(401, "Session expired")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(sent, s.csrf_token):
            raise HTTPException(403, "CSRF token missing or invalid")
    return s


def current_user(s: AuthSession = Depends(current_session), db: Session = Depends(get_db)) -> User:
    user = db.get(User, s.user_id)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


@dataclass
class Ctx:
    db: Session
    user: User
    workspace_id: int
    role: str

    def can(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


def ws_member(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Ctx:
    m = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id))
    if not m:
        raise HTTPException(404, "Workspace not found")
    return Ctx(db=db, user=user, workspace_id=workspace_id, role=m.role)


def require_role(min_role: str):
    def dep(ctx: Ctx = Depends(ws_member)) -> Ctx:
        if not ctx.can(min_role):
            raise HTTPException(403, f"Requires {min_role} role")
        return ctx

    return dep


ws_manager = require_role("manager")
ws_admin = require_role("admin")
