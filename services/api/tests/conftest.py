import os

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://test:test@localhost:5433/techpm_test")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "kP3m8o5QqVwV9a4t1xT0tX1u0m6Qk2yZ0hZzv7mJj1c=")
os.environ["APP_ENV"] = "test"
os.environ["LLM_PROVIDER"] = "demo"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.deps import auth_limiter
from app.core.db import Base, get_engine, session_factory
from app.main import app
from app.services.seed import DEMO_PASSWORD, seed_demo


@pytest.fixture(scope="session", autouse=True)
def migrated():
    """Run the real Alembic migrations (not create_all) so migration drift fails the suite."""
    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("drop schema public cascade; create schema public"))
    command.upgrade(Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini")), "head")
    yield


@pytest.fixture(autouse=True)
def clean(migrated):
    auth_limiter.reset()
    eng = get_engine()
    with eng.begin() as c:
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        c.execute(text(f"truncate {names} restart identity cascade"))
    yield


@pytest.fixture
def db():
    with session_factory()() as s:
        yield s


@pytest.fixture
def client():
    with TestClient(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def seeded(db):
    return seed_demo(db)


class Session:
    """Authenticated API helper that sends the CSRF header automatically."""

    def __init__(self, client: TestClient, email: str, password: str = DEMO_PASSWORD):
        self.c = client
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.me = r.json()
        self.csrf = self.me["csrf_token"]
        self.ws = self.me["workspaces"][0]["id"] if self.me["workspaces"] else None

    def req(self, method, path, **kw):
        headers = {"x-csrf-token": self.csrf, **kw.pop("headers", {})}
        return self.c.request(method, f"/api/workspaces/{self.ws}{path}", headers=headers, **kw)

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, **kw):
        return self.req("POST", path, **kw)


def login_as(client, role, **kw):
    return Session(client, f"{role}@demo.example", **kw)


@pytest.fixture
def manager(client, seeded):
    return login_as(client, "manager")
