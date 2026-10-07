import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import (COOKIE_NAME, auth_limiter, create_session, current_session, current_user, hash_password,
                           verify_password)
from app.core.config import get_settings
from app.core.db import get_db
from app.models import AuthSession, Membership, User, Workspace
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


def _set_cookie(resp: Response, raw: str) -> None:
    st = get_settings()
    resp.set_cookie(COOKIE_NAME, raw, httponly=True, secure=st.cookie_secure, samesite="lax",
                    max_age=st.session_ttl_hours * 3600, path="/")


def _me(db: Session, user: User, sess: AuthSession) -> dict:
    rows = db.execute(select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id)
                      .where(Membership.user_id == user.id)).all()
    return {"user": {"id": user.id, "email": user.email, "name": user.name}, "csrf_token": sess.csrf_token,
            "demo_mode": get_settings().demo_mode,
            "workspaces": [{"id": w.id, "name": w.name, "role": r} for w, r in rows]}


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    auth_limiter.check(f"reg:{request.client.host if request.client else 'x'}")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    slug = re.sub(r"[^a-z0-9]+", "-", body.workspace_name.lower()).strip("-")[:60] or "workspace"
    base, n = slug, 1
    while db.scalar(select(Workspace.id).where(Workspace.slug == slug)):
        n += 1
        slug = f"{base}-{n}"
    ws = Workspace(name=body.workspace_name.strip(), slug=slug, settings={})
    db.add(ws)
    db.flush()
    db.add(Membership(workspace_id=ws.id, user_id=user.id, role="admin"))
    audit(db, ws.id, "user.registered", actor_user_id=user.id)
    raw, sess = create_session(db, user)
    db.commit()
    _set_cookie(response, raw)
    return _me(db, user, sess)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower()
    auth_limiter.check(f"login:{request.client.host if request.client else 'x'}:{email}")
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    raw, sess = create_session(db, user)
    db.commit()
    _set_cookie(response, raw)
    return _me(db, user, sess)


@router.post("/logout", status_code=204)
def logout(response: Response, sess: AuthSession = Depends(current_session), db: Session = Depends(get_db)):
    db.delete(sess)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me")
def me(user: User = Depends(current_user), sess: AuthSession = Depends(current_session), db: Session = Depends(get_db)):
    return _me(db, user, sess)
