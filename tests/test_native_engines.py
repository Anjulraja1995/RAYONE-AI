import os
os.environ["RAYONE_DB"]="/tmp/rayone_native_engine_test.db"
try: os.remove(os.environ["RAYONE_DB"])
except FileNotFoundError: pass
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)

def auth():
    r=client.post("/api/auth/login",json={"password":"RAYONE-Admin-2026"})
    assert r.status_code==200
    return {"Authorization":"Bearer "+r.json()["token"]}

def test_native_engines():
    h=auth()
    s=client.get("/api/v2/native/status",headers=h)
    assert s.status_code==200 and s.json()["engine"]=="rayone-native"
    q=client.post("/api/v2/native/search",headers=h,json={"query":"rayone"})
    assert q.status_code==200 and q.json()["engine"]=="rayone-native-local-index"
    d=client.post("/api/v2/native/data/csv",headers=h,json={"text":"name,value\na,2\nb,4\n"})
    assert d.status_code==200 and d.json()["rows"]==2 and d.json()["numeric"]["value"]["mean"]==3
    c=client.post("/api/v2/native/code/analyze",headers=h,json={"source":"def hello():\n    return 1\n"})
    assert c.status_code==200 and c.json()["syntax_ok"] and "hello" in c.json()["functions"]
    bad=client.post("/api/v2/native/code/analyze",headers=h,json={"source":"def broken(:\n"})
    assert bad.status_code==200 and not bad.json()["syntax_ok"]
    w=client.post("/api/v2/native/workflow/plan",headers=h,json={"steps":[{"type":"set","key":"name","value":"RAYONE"},{"type":"transform","key":"name","operation":"lower"}]})
    assert w.status_code==200 and w.json()["data"]["name"]=="rayone"

    t2=client.post("/api/v2/tools/run",headers=h,json={"name":"local.advanced_native.code_analyze","args":{"source":"x=1"}})
    assert t2.status_code==200 and t2.json()["result"]["syntax_ok"]
