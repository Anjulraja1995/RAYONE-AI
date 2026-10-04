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


def test_conversational_pending_followup_and_status():
    h=login()
    cid="final-conversation-followup"
    q=client.post("/api/v2/assistant/chat",headers=h,json={
        "message":"Tum ek nature image bana sakte ho","conversation_id":cid,
        "request_id":"capability-question"
    })
    assert q.status_code==200 and q.json().get("pending") is True
    run=client.post("/api/v2/assistant/chat",headers=h,json={
        "message":"Banao","conversation_id":cid,"request_id":"pending-execution"
    })
    assert run.status_code==200 and run.json().get("intent")=="media"
    assert run.json().get("result",{}).get("size",0)>0
    status=client.post("/api/v2/assistant/chat",headers=h,json={
        "message":"Ky hua","conversation_id":cid,"request_id":"status-followup"
    })
    assert status.status_code==200 and "previous" in status.json().get("answer","").lower()

def test_planning_is_executed_not_chat_fallback():
    h=login()
    r=client.post("/api/v2/assistant/chat",headers=h,json={
        "message":"Make a plan for launching RAYONE","request_id":"planning-execution"
    })
    assert r.status_code==200 and r.json().get("intent")=="planning"
    assert r.json().get("result",{}).get("steps")


def test_natural_language_routes_to_local_capabilities():
    h=login()
    a=client.post("/api/v2/assistant/chat",headers=h,json={"message":"Convert 10 km to miles","request_id":"natural-conversion"})
    assert a.status_code==200 and a.json().get("intent")=="tool"
    assert a.json().get("result") is not None
    b=client.post("/api/v2/assistant/chat",headers=h,json={"message":"Translate hello to Hindi","request_id":"natural-translation"})
    assert b.status_code==200 and b.json().get("intent")=="tool"


def test_natural_arithmetic_without_command_word():
    h=login()
    r=client.post("/api/v2/assistant/chat",headers=h,json={"message":"What is 125 * 48?","request_id":"natural-arithmetic"})
    assert r.status_code==200 and r.json().get("intent")=="tool"
    assert r.json().get("result")==6000


def test_explicit_compound_request_executes_each_step():
    h=login()
    r=client.post("/api/v2/assistant/chat",headers=h,json={
        "message":"Calculate 10 + 5 then calculate 20 * 3","request_id":"compound-execution"
    })
    assert r.status_code==200, r.text
    d=r.json()
    assert d.get("state")=="Complete" and d.get("intent")=="workflow"
    steps=d.get("result",{}).get("steps",[])
    assert len(steps)==2
    assert steps[0].get("result")==15
    assert steps[1].get("result")==60
