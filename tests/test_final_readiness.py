import os, json
os.environ["RAYONE_DB"]="/tmp/rayone_final_readiness.db"
try: os.remove(os.environ["RAYONE_DB"])
except FileNotFoundError: pass
from fastapi.testclient import TestClient
from app.main import app
from app.local_tools import TOTAL_CAPABILITIES

client=TestClient(app)
def login():
    r=client.post("/api/auth/login",json={"password":"RAYONE-Admin-2026"})
    assert r.status_code==200
    return {"Authorization":"Bearer "+r.json()["token"]}
def test_final_public_health_and_protected_surface():
    assert client.get("/api/health").json()["ok"] is True
    h=login()
    for path in ["/api/v2/status","/api/v2/capabilities","/api/v2/workspace/stats",
                 "/api/v2/backup/export","/api/v2/production/status",
                 "/api/v2/production/creative","/api/v2/production/hardening"]:
        r=client.get(path,headers=h); assert r.status_code==200, (path,r.text)
def test_capability_and_execution_smoke():
    h=login()
    r=client.get("/api/v2/tools/catalog",headers=h)
    assert r.status_code==200 and r.json()["count"]==1144
    for name,args in [
        ("local.text.upper",{"text":"rayone"}),
        ("local.math.sum",{"values":[2,3]}),
        ("local.json.parse",{"text":"{\"b\":2,\"a\":1}"}),
    ]:
        r=client.post("/api/v2/tools/run",headers=h,json={"name":name,"args":args})
        assert r.status_code==200 and r.json().get("state")=="Complete", (name,r.text)
def test_execution_idempotency_and_auth_boundary():
    h=login()
    p={"name":"local.math.sum","args":{"values":[10,5]},"request_id":"final-readiness-idempotent"}
    a=client.post("/api/v2/tools/run",headers=h,json=p); b=client.post("/api/v2/tools/run",headers=h,json=p)
    assert a.status_code==200 and b.status_code==200 and b.json()["result"]==15
    assert client.get("/api/summary").status_code==401
    assert client.get("/api/v2/production/status").status_code==401
def test_native_creative_artifacts():
    h=login()
    for name in ["local.image.generate","local.design.generate","local.audio.generate","local.music.generate","local.voice.generate","local.video.generate"]:
        r=client.post("/api/v2/tools/run",headers=h,json={"name":name,"args":{"prompt":"final readiness"}})
        assert r.status_code==200 and r.json()["result"]["size"]>0
