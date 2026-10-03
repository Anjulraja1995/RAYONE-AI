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
