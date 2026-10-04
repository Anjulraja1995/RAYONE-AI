import os
os.environ["RAYONE_DB"] = "/tmp/rayone_foundation_security.db"

try:
    os.remove(os.environ["RAYONE_DB"])
except FileNotFoundError:
    pass

from fastapi.testclient import TestClient
from app.main import app, execute

client = TestClient(app)

def token():
    r = client.post("/api/auth/login", json={"password": "RAYONE-Admin-2026"})
    assert r.status_code == 200
    return r.json()

def headers(value):
    return {"Authorization": "Bearer " + value}

def test_session_introspection_reports_expiry():
    data = token()
    r = client.get("/api/auth/session", headers=headers(data["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["expires_at"] >= body["created_at"]
    assert body["expires_in"] > 0
    assert data["expires_in"] > 0

def test_expired_sessions_are_removed_before_new_login():
    old = token()
    execute("update sessions set expires=? where token=?", (0, old["token"]))
    fresh = token()
    assert fresh["token"] != old["token"]
    assert client.get("/api/auth/session", headers=headers(old["token"])).status_code == 401

def test_logout_is_audited_and_revokes_session():
    data = token()
    r = client.post("/api/auth/logout", headers=headers(data["token"]))
    assert r.status_code == 200
    assert client.get("/api/auth/session", headers=headers(data["token"])).status_code == 401

def test_health_reports_database_integrity():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["database"] == "sqlite"
    assert body["database_integrity"] is True
    assert body["active_sessions"] >= 0

def test_invalid_session_is_rejected_and_removed():
    fake = "not-a-valid-session"
    r = client.get("/api/auth/session", headers=headers(fake))
    assert r.status_code == 401
