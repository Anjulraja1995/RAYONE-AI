import os
os.environ["RAYONE_DB"]="/tmp/rayone_production_test.db"
try: os.remove(os.environ["RAYONE_DB"])
except FileNotFoundError: pass
from fastapi.testclient import TestClient
from app.main import app
from app.provider_adapters import _chat_url, _models_url
from app.connector_adapters import normalize_kind

client=TestClient(app)

def token():
    r=client.post("/api/auth/login",json={"password":"RAYONE-Admin-2026"})
    assert r.status_code==200
    return r.json()["token"]

def h(t): return {"Authorization":"Bearer "+t}

def test_adapter_contracts():
    assert _chat_url("http://x","ollama").endswith("/api/chat")
    assert _chat_url("http://x","openai_compatible").endswith("/chat/completions")
    assert _models_url("http://x","ollama").endswith("/api/tags")
    assert normalize_kind("rest")=="http"

def test_production_status():
    t=token(); r=client.get("/api/v2/production/status",headers=h(t))
    assert r.status_code==200 and r.json()["providers"] is True

def test_hardening():
    t=token(); r=client.get("/api/v2/production/hardening",headers=h(t))
    assert r.status_code==200 and r.json()["ok"] is True

def test_creative_info():
    t=token(); r=client.get("/api/v2/production/creative",headers=h(t))
    assert r.status_code==200 and "capabilities" in r.json()
