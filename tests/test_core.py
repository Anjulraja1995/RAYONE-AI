import os
os.environ["RAYONE_DB"] = "/tmp/rayone_test.db"
try: os.remove(os.environ["RAYONE_DB"])
except FileNotFoundError: pass
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def token():
    r=client.post('/api/auth/login',json={'password':'RAYONE-Admin-2026'})
    assert r.status_code==200
    return r.json()['token']

def h(t): return {'Authorization':'Bearer '+t}

def test_health(): assert client.get('/api/health').json()['ok'] is True

def test_auth_and_summary():
    t=token();r=client.get('/api/summary',headers=h(t));assert r.status_code==200

def test_tool():
    t=token();r=client.post('/api/tools/execute',headers=h(t),json={'name':'core.echo','args':{'text':'ok'}});assert r.status_code==200 and r.json()['result']=='ok'

def test_calculator():
    t=token();r=client.post('/api/tools/execute',headers=h(t),json={'name':'core.calculator','args':{'expression':'2+3*4'}});assert r.json()['result']==14

def test_memory():
    t=token();client.post('/api/memory',headers=h(t),json={'content':'rayone memory test'});r=client.get('/api/memory/search?q=rayone',headers=h(t));assert any('rayone memory test' in x['content'] for x in r.json())

def test_chat():
    t=token();r=client.post('/api/chat',headers=h(t),json={'message':'hello'});assert r.status_code==200 and 'answer' in r.json()

def test_project_crud():
    t=token();r=client.post('/api/projects',headers=h(t),json={'name':'Test'});assert r.status_code==200


def test_v2_status_and_assistant():
    t=token()
    h2=h(t)
    s=client.get('/api/v2/status',headers=h2)
    assert s.status_code==200 and s.json()['core']=='online'
    a=client.post('/api/v2/assistant/chat',headers={**h2,'Content-Type':'application/json'},json={'message':'calculate 2+2'})
    assert a.status_code==200 and a.json()['result']==4

def test_v2_permissions_and_schedule():
    t=token(); hh=h(t)
    p=client.post('/api/v2/permissions',headers={**hh,'Content-Type':'application/json'},json={'capability':'tool.execute','scope':'core'})
    assert p.status_code==200
    s=client.post('/api/v2/automation/schedules',headers={**hh,'Content-Type':'application/json'},json={'name':'test','expression':'3600','action':'chat','payload':{'message':'hello'}})
    assert s.status_code==200


def test_v2_capabilities_and_backup():
    t=token(); hh=h(t)
    c=client.get('/api/v2/capabilities',headers=hh)
    assert c.status_code==200 and 'provider_failover' in c.json()['v2']
    b=client.get('/api/v2/backup/export',headers=hh)
    assert b.status_code==200 and 'tables' in b.json()

def test_v2_job_cancel_retry_and_workspace_stats():
    t=token(); hh=h(t)
    j=client.post('/api/jobs',headers={**hh,'Content-Type':'application/json'},json={'type':'unknown','input':{}})
    assert j.status_code==200
    jid=j.json()['id']
    c=client.post('/api/v2/jobs/'+jid+'/cancel',headers=hh)
    assert c.status_code==200 and c.json()['status']=='cancelled'
    r=client.post('/api/v2/jobs/'+jid+'/retry',headers=hh)
    assert r.status_code==200 and r.json()['status']=='queued'
    s=client.get('/api/v2/workspace/stats',headers=hh)
    assert s.status_code==200 and 'files' in s.json()

def test_v2_cron_validation():
    t=token(); hh=h(t)
    r=client.post('/api/v2/automation/cron',headers={**hh,'Content-Type':'application/json'},json={'name':'cron-test','expression':'* * * * *','action':'chat','payload':{'message':'hello'}})
    assert r.status_code==200 and 'next_run' in r.json()


def test_v2_conversation_persistence():
    t=token(); hh=h(t)
    r=client.post('/api/v2/assistant/chat',headers=hh,json={'message':'hello persistence'})
    assert r.status_code==200 and r.json().get('conversation_id')
    cid=r.json()['conversation_id']
    m=client.get('/api/v2/conversations/'+cid+'/messages',headers=hh)
    assert m.status_code==200 and len(m.json())>=2

def test_password_hash_roundtrip():
    from app.main import hash_password, verify_password
    encoded=hash_password("A-strong-test-password-2026")
    assert encoded.startswith("scrypt$")
    assert verify_password("A-strong-test-password-2026",encoded)
    assert not verify_password("wrong-password",encoded)


def test_local_tool_pack():
    t=token(); hh=h(t)
    r=client.get('/api/v2/tools/catalog',headers=hh)
    assert r.status_code==200 and r.json()['count'] >= 1100
    assert r.json()['count'] == 1138
    r=client.post('/api/v2/tools/run',headers={**hh,'Content-Type':'application/json'},json={'name':'local.text.slug','args':{'text':'Hello RAYONE World'}})
    assert r.status_code==200 and r.json()['result']=='hello-rayone-world'

def test_expanded_local_capabilities():
    t=token(); hh=h(t)
    for name,args in [
        ('local.finance.discount',{'value':100,'rate':10}),
        ('local.geometry.circle_area',{'value':2}),
        ('local.colors.hex_to_rgb',{'value':'#ff0000'}),
        ('local.conversion.celsius_fahrenheit',{'value':0}),
        ('local.security.safe_filename',{'value':'hello world?.txt'}),
    ]:
        r=client.post('/api/v2/tools/run',headers={**hh,'Content-Type':'application/json'},json={'name':name,'args':args})
        assert r.status_code==200


def test_local_adapters_import_and_media_probe(tmp_path):
    from app.local_adapters import media_probe, extract_text
    p=tmp_path / "sample.txt"; p.write_text("hello")
    r=media_probe(p)
    assert r["name"]=="sample.txt" and r["kind"]=="text"
    assert extract_text("<p>Hello</p>")=="Hello"


def test_workflow_validation():
    t=token(); hh=h(t)
    r=client.post('/api/v2/workflows/validate',headers={**hh,'Content-Type':'application/json'},json={'steps':[{'type':'tool','name':'local.text.upper','args':{'text':'hello'}}]})
    assert r.status_code==200 and r.json()['valid'] is True


def test_vorqyon_execute_and_verify():
    t=token(); hh=h(t)
    r=client.post('/api/v2/vorqyon/execute',headers={**hh,'Content-Type':'application/json'},json={'mode':'tool','target':'local.math.sum','args':{'values':[1,2,3]},'expected':6})
    assert r.status_code==200 and r.json()['verification']['ok'] is True


def test_scheduler_once_is_single_shot_and_supports_tools():
    import time
    t=token(); hh=h(t)
    r=client.post('/api/v2/automation/once',headers={**hh,'Content-Type':'application/json'},json={
        'name':'once-tool','expression':str(time.time()-1),'action':'tool',
        'payload':{'name':'local.math.sum','args':{'values':[2,3]}}
    })
    assert r.status_code==200
    sid=r.json()['id']
    first=client.post('/api/v2/automation/run-due',headers=hh)
    assert first.status_code==200 and any(x['id']==sid and x['ok'] for x in first.json())
    schedules=client.get('/api/v2/automation/schedules',headers=hh).json()
    row=next(x for x in schedules if x['id']==sid)
    assert row['enabled']==0 and row['next_run'] is None
    second=client.post('/api/v2/automation/run-due',headers=hh)
    assert second.status_code==200 and not any(x['id']==sid for x in second.json())


def test_local_media_probe_endpoint():
    t=token(); hh=h(t)
    r=client.post('/api/v2/files/upload',headers=hh,files={'file':('probe.txt',b'hello','text/plain')})
    assert r.status_code==200
    fid=r.json()['id']
    p=client.post('/api/v2/local/media/probe',headers={**hh,'Content-Type':'application/json'},json={'file_id':fid})
    assert p.status_code==200 and p.json()['adapter']=='local-media-probe'


def test_vorqyon_approval_gate_executes_after_approval():
    t=token(); hh=h(t)
    r=client.post('/api/v2/vorqyon/execute',headers={**hh,'Content-Type':'application/json'},json={
        'mode':'tool','target':'local.math.sum','args':{'values':[4,5]},'expected':9,'require_approval':True
    })
    assert r.status_code==200 and r.json()['state']=='Awaiting Approval'
    aid=r.json()['approval_id']
    d=client.post('/api/v2/approvals/'+aid,headers={**hh,'Content-Type':'application/json'},json={'decision':'approved'})
    assert d.status_code==200 and d.json()['status']=='approved'
    assert d.json()['result']['execution']==9
    assert d.json()['result']['verification']['ok'] is True

def test_native_creative_tools_execute(tmp_path):
    t=token(); hh=h(t)
    for name, marker in [
        ("local.image.generate", "image/svg+xml"),
        ("local.design.generate", "image/svg+xml"),
        ("local.audio.generate", "audio/wav"),
        ("local.music.generate", "audio/wav"),
        ("local.voice.generate", "audio/wav"),
        ("local.video.generate", "image/svg+xml"),
    ]:
        r=client.post("/api/v2/tools/run",headers={**hh,"Content-Type":"application/json"},
                      json={"name":name,"args":{"prompt":"RAYONE native test"}})
        assert r.status_code==200, r.text
        data=r.json()["result"]
        assert data["native"] is True
        assert data["mime"]==marker
        assert data["size"]>0

def test_native_media_job_completes_without_provider():
    t=token(); hh=h(t)
    r=client.post("/api/v2/media",headers={**hh,"Content-Type":"application/json"},
                  json={"kind":"image","input":{"prompt":"native media job"}})
    assert r.status_code==200, r.text
    data=r.json()
    assert data["status"]=="completed"
    assert data["provider"]=="native"
    assert data["result"]["native"] is True
