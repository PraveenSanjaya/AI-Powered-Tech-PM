from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import login_as


def register(c, email, ws="Other Org"):
    r = c.post("/api/auth/register", json={"email": email, "name": "Other", "password": "a-long-password-1",
                                           "workspace_name": ws})
    assert r.status_code == 201, r.text
    return r.json()


def test_login_sets_httponly_cookie_and_hides_secrets(client, seeded):
    r = client.post("/api/auth/login", json={"email": "manager@demo.example", "password": "demo-password-123"})
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc
    assert "password" not in r.text.lower()


def test_wrong_password_and_unauthenticated(client, seeded):
    bad = {"email": "manager@demo.example", "password": "nope-nope-nope"}
    assert client.post("/api/auth/login", json=bad).status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/workspaces/1/projects").status_code == 401


def test_login_rate_limited(client, seeded):
    bad = {"email": "manager@demo.example", "password": "bad-password-x"}
    codes = [client.post("/api/auth/login", json=bad).status_code for _ in range(10)]
    assert 429 in codes and codes[0] == 401


def test_csrf_required_for_cookie_authenticated_mutations(client, manager):
    r = client.post(f"/api/workspaces/{manager.ws}/projects/1/risks/scan")  # cookie but no CSRF header
    assert r.status_code == 403 and "CSRF" in r.text
    assert manager.post("/projects/1/risks/scan").status_code == 200


def test_role_enforcement(client, seeded):
    member = login_as(client, "member")
    assert member.get("/projects/1/dashboard").status_code == 200  # read allowed
    assert member.post("/projects/1/risks/scan").status_code == 403
    assert member.req("PATCH", "/settings", json={"risk_thresholds": {"stale_days": 5}}).status_code == 403
    assert member.get("/audit").status_code == 403
    mgr = login_as(client, "manager")
    assert mgr.req("PATCH", "/settings", json={"risk_thresholds": {"stale_days": 5}}).status_code == 403
    admin = login_as(client, "admin")
    assert admin.req("PATCH", "/settings", json={"risk_thresholds": {"stale_days": 5}}).status_code == 200
    assert admin.req("PATCH", "/settings", json={"risk_thresholds": {"stale_days": -1}}).status_code == 422
    assert admin.req("PATCH", "/settings", json={"risk_thresholds": {"bogus": 1}}).status_code == 422


def test_cross_workspace_isolation(seeded):
    with TestClient(app) as a, TestClient(app) as b:
        mgr = login_as(a, "manager")
        conv = mgr.post("/conversations", json={"project_id": 1}).json()
        mgr.post(f"/conversations/{conv['id']}/messages", json={"text": "draft a jira comment for ATLAS-101"})
        acts = mgr.get("/actions").json()
        assert acts
        other = register(b, "attacker@other.example")
        ows, h = other["workspaces"][0]["id"], {"x-csrf-token": other["csrf_token"]}
        # guessing the victim workspace id: not a member -> 404
        for path in (f"/{mgr.ws}/projects", f"/{mgr.ws}/projects/1/dashboard", f"/{mgr.ws}/actions/{acts[0]['id']}",
                     f"/{mgr.ws}/integrations", f"/{mgr.ws}/audit"):
            assert b.get(f"/api/workspaces{path}").status_code == 404, path
        r = b.post(f"/api/workspaces/{mgr.ws}/actions/{acts[0]['id']}/approve", json={"version": 1}, headers=h)
        assert r.status_code == 404
        # own workspace + victim record ids: not found
        assert b.get(f"/api/workspaces/{ows}/projects/1/issues").status_code == 404
        assert b.get(f"/api/workspaces/{ows}/actions/{acts[0]['id']}").status_code == 404
        assert b.get(f"/api/workspaces/{ows}/conversations/{conv['id']}/messages").status_code == 404
        p = b.post(f"/api/workspaces/{ows}/projects", json={"key": "OTH", "name": "Other"}, headers=h).json()
        assert b.get(f"/api/workspaces/{ows}/projects/{p['id']}/issues/1").status_code == 404
        assert b.get(f"/api/workspaces/{ows}/projects/{p['id']}/issues").json()["total"] == 0


def test_conversations_are_private_to_their_owner(seeded):
    with TestClient(app) as a, TestClient(app) as b:
        mgr = login_as(a, "manager")
        conv = mgr.post("/conversations", json={"project_id": 1}).json()
        admin = login_as(b, "admin")
        assert admin.get(f"/conversations/{conv['id']}/messages").status_code == 404
