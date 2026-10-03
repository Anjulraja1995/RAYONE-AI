
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pathlib import Path
import asyncio, base64, hashlib, json, mimetypes, os, re, time, uuid
from . import main as legacy

router = APIRouter(prefix="/api/v2", tags=["RAYONE v2 control plane"])
ROOT = legacy.ROOT
STORE = ROOT / "data" / "workspace"
STORE.mkdir(parents=True, exist_ok=True)

def db():
    return legacy.conn()

def init_advanced():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS traces(id TEXT PRIMARY KEY, request_id TEXT, state TEXT, actor TEXT, payload TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, action TEXT, target TEXT, payload TEXT, status TEXT, created REAL, decided REAL);
    CREATE TABLE IF NOT EXISTS permissions(id TEXT PRIMARY KEY, subject TEXT, capability TEXT, scope TEXT, effect TEXT, created REAL, UNIQUE(subject,capability,scope));
    CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, name TEXT, kind TEXT, expression TEXT, action TEXT, payload TEXT, enabled INTEGER, next_run REAL, last_run REAL, created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS workspace_files(id TEXT PRIMARY KEY, name TEXT, path TEXT, mime TEXT, size INTEGER, sha256 TEXT, created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS media_jobs(id TEXT PRIMARY KEY, kind TEXT, status TEXT, input TEXT, output TEXT, provider TEXT, created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS connectors(id TEXT PRIMARY KEY, name TEXT, kind TEXT, base_url TEXT, enabled INTEGER, config TEXT, created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS metrics_v2(key TEXT PRIMARY KEY, value REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS memory_index(id TEXT PRIMARY KEY, memory_id TEXT, tokens TEXT, fingerprint TEXT, created REAL);
    """)
    c.commit(); c.close()

init_advanced()

def auth(token=Depends(legacy.auth)): return token
def j(x): return json.loads(x or "{}") if isinstance(x,str) else (x or {})
def audit(action,target,detail): legacy.audit(action,target,detail)

def trace(state, request_id, payload=None):
    tid=str(uuid.uuid4())
    legacy.execute("insert into traces values(?,?,?,?,?,?)",(tid,request_id,state,"admin",legacy.dumps(payload or {}),legacy.now()))
    legacy.emit("rayone.state",{"trace_id":tid,"request_id":request_id,"state":state})
    return tid

def metric(key,delta=1):
    legacy.execute("insert into metrics_v2(key,value,updated) values(?,?,?) on conflict(key) do update set value=value+excluded.value,updated=excluded.updated",(key,delta,legacy.now()))

class AssistantIn(BaseModel):
    message: str = Field(min_length=1)
    project_id: str|None=None
    model_id: str|None=None
    require_approval: bool=False

class ApprovalIn(BaseModel):
    decision: str

class PermissionIn(BaseModel):
    subject: str="admin"
    capability: str
    scope: str="global"
    effect: str="allow"

class ScheduleIn(BaseModel):
    name: str
    kind: str="interval"
    expression: str="3600"
    action: str="chat"
    payload: dict={}
    enabled: bool=True

class ConnectorIn(BaseModel):
    name: str
    kind: str
    base_url: str=""
    config: dict={}
    enabled: bool=True

class MediaIn(BaseModel):
    kind: str
    input: dict={}
    provider: str=""

@router.get("/status")
def status(_:str=Depends(auth)):
    counts={t:legacy.rows(f"select count(*) n from {t}")[0]["n"] for t in ["projects","providers","models","tools","agents","workflows","memories","jobs","events","audits","checkpoints"]}
    advanced={t:legacy.rows(f"select count(*) n from {t}")[0]["n"] for t in ["traces","approvals","permissions","schedules","workspace_files","media_jobs","connectors"]}
    return {"version":legacy.APP_VERSION,"core":"online","vorqyon":"ready","state_machine":["Idle","Understanding","Planning","Researching","Tool Use","Executing","Verifying","Complete"],"counts":counts|advanced,"local_first":True}

@router.get("/trace")
def traces(limit:int=200,_:str=Depends(auth)):
    return legacy.rows("select * from traces order by created desc limit ?",(min(limit,500),))

@router.get("/metrics")
def metrics(_:str=Depends(auth)):
    return legacy.rows("select * from metrics_v2 order by key")

@router.get("/permissions")
def permissions(_:str=Depends(auth)):
    return legacy.rows("select * from permissions order by subject,capability")

@router.post("/permissions")
def add_permission(x:PermissionIn,_:str=Depends(auth)):
    i=str(uuid.uuid4())
    legacy.execute("insert or replace into permissions values(?,?,?,?,?,?)",(i,x.subject,x.capability,x.scope,x.effect,legacy.now()))
    audit("permission.set",x.capability,x.model_dump())
    return {"id":i}

@router.delete("/permissions/{id}")
def delete_permission(id:str,_:str=Depends(auth)):
    legacy.execute("delete from permissions where id=?",(id,)); return {"ok":True}

@router.get("/approvals")
def approvals(_:str=Depends(auth)):
    return legacy.rows("select * from approvals order by created desc limit 200")

@router.post("/approvals/{id}")
async def decide_approval(id:str,x:ApprovalIn,_:str=Depends(auth)):
    if x.decision not in {"approved","rejected"}: raise HTTPException(400,"decision must be approved or rejected")
    item=legacy.one("select * from approvals where id=?",(id,))
    if not item: raise HTTPException(404,"Approval not found")
    if item["status"]!="pending": raise HTTPException(409,"Approval already decided")
    result=None
    if x.decision=="approved" and item["action"].startswith("github."):
        payload=j(item["payload"]); method=item["action"].split(".",1)[1]
        path=str(payload.get("path",""))
        if not _gh_allowed(path): raise HTTPException(400,"GitHub path not allowed")
        if not os.getenv("GITHUB_TOKEN"): raise HTTPException(503,"GITHUB_TOKEN is not configured")
        headers=_gh_headers()
        body=payload.get("body") or {}
        async with legacy.httpx.AsyncClient(timeout=30) as c:
            r=await c.request(method,"https://api.github.com"+path,headers=headers,json=body)
            result={"status":r.status_code,"data":r.json() if "application/json" in r.headers.get("content-type","") else r.text[:20000]}
            if r.status_code>=400: raise HTTPException(r.status_code,"GitHub request failed: "+r.text[:2000])
    legacy.execute("update approvals set status=?,decided=? where id=?",(x.decision,legacy.now(),id))
    audit("approval."+x.decision,"approval",{"id":id,"result":result})
    return {"ok":True,"status":x.decision,"result":result}

def classify(message):
    m=message.lower().strip()
    if re.search(r"\b(search|research|find|latest|news|web)\b",m): return "research"
    if re.search(r"\b(calculate|compute|math|sum|add|subtract|multiply|divide)\b",m): return "tool"
    if re.search(r"\b(schedule|remind|every day|every hour|cron)\b",m): return "automation"
    if re.search(r"\b(image|video|audio|music|voice|tts|speech)\b",m): return "media"
    if re.search(r"\b(file|document|pdf|docx|xlsx|upload)\b",m): return "knowledge"
    if re.search(r"\b(github|gitlab|repository|commit|pull request|issue)\b",m): return "devops"
    return "chat"

async def research(url_or_query):
    if re.match(r"^https?://",url_or_query):
        async with legacy.httpx.AsyncClient(timeout=20,follow_redirects=False) as c:
            r=await c.get(url_or_query); return {"source":url_or_query,"status":r.status_code,"content":r.text[:20000]}
    return {"query":url_or_query,"status":"search_adapter_required","results":[]}

async def assistant_run(x):
    rid=str(uuid.uuid4()); metric("assistant.requests")
    trace("Idle",rid)
    trace("Understanding",rid,{"message":x.message})
    intent=classify(x.message)
    trace("Planning",rid,{"intent":intent})
    if x.require_approval:
        aid=str(uuid.uuid4())
        legacy.execute("insert into approvals values(?,?,?,?,?,?,?)",(aid,"assistant_action",intent,legacy.dumps(x.model_dump()),"pending",legacy.now(),None))
        trace("Complete",rid,{"approval_required":True,"approval_id":aid})
        return {"request_id":rid,"state":"Awaiting Approval","approval_id":aid}
    if intent=="tool":
        expr=re.sub(r"^(please\s+)?(calculate|compute)\s+","",x.message.strip(),flags=re.I)
        trace("Tool Use",rid,{"tool":"core.calculator"})
        result=await legacy.execute_tool_internal("core.calculator",{"expression":expr})
        trace("Verifying",rid,{"result":result})
        trace("Complete",rid)
        return {"request_id":rid,"state":"Complete","intent":intent,"result":result}
    if intent=="research":
        trace("Researching",rid)
        result=await research(x.message.strip())
        trace("Verifying",rid,{"sources":1 if result.get("source") else 0})
        trace("Complete",rid)
        return {"request_id":rid,"state":"Complete","intent":intent,"result":result}
    if intent=="media":
        trace("Executing",rid,{"media":True})
        mid=str(uuid.uuid4()); t=legacy.now()
        legacy.execute("insert into media_jobs values(?,?,?,?,?,?,?,?)",(mid,"generic","queued",legacy.dumps(x.message),None,None,t,t))
        trace("Verifying",rid,{"media_job_id":mid})
        trace("Complete",rid)
        return {"request_id":rid,"state":"Complete","intent":intent,"media_job_id":mid,"message":"Media job queued; provider adapter can be attached without changing the core contract."}
    trace("Executing",rid)
    answer,provider=await legacy.provider_chat(x.message,x.model_id)
    if answer is None:
        mem=legacy.rows("select content from memories where content like ? order by created desc limit 5",(f"%{x.message[:40]}%",))
        answer="RAYONE local core received: "+x.message
        if mem: answer+="\nRelevant memory: "+" ".join(m["content"] for m in mem)
        provider="local"
    trace("Verifying",rid,{"provider":provider})
    trace("Complete",rid)
    metric("assistant.completed")
    return {"request_id":rid,"state":"Complete","intent":intent,"provider":provider,"answer":answer}

@router.post("/assistant/chat")
async def assistant_chat(x:AssistantIn,_:str=Depends(auth)):
    try: return await assistant_run(x)
    except Exception as e:
        metric("assistant.errors"); raise HTTPException(500,str(e))

@router.post("/assistant/stream")
async def assistant_stream(x:AssistantIn,_:str=Depends(auth)):
    result=await assistant_run(x)
    async def gen():
        yield "event: state\ndata: "+json.dumps({"state":"Complete","request_id":result.get("request_id")})+"\n\n"
        yield "event: result\ndata: "+json.dumps(result,ensure_ascii=False)+"\n\n"
        yield "event: done\ndata: {}\n\n"
    return StreamingResponse(gen(),media_type="text/event-stream")

@router.post("/research")
async def do_research(payload:dict,_:str=Depends(auth)):
    q=str(payload.get("query","")).strip()
    if not q: raise HTTPException(400,"query required")
    return await research(q)

@router.get("/automation/schedules")
def schedules(_:str=Depends(auth)):
    return legacy.rows("select * from schedules order by created desc")

@router.post("/automation/schedules")
def add_schedule(x:ScheduleIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=legacy.now()
    seconds=float(x.expression) if x.kind=="interval" and str(x.expression).replace(".","",1).isdigit() else 3600
    legacy.execute("insert into schedules values(?,?,?,?,?,?,?,?,?,?,?)",(i,x.name,x.kind,x.expression,x.action,legacy.dumps(x.payload),int(x.enabled),t+seconds,None,t,t))
    audit("schedule.create","schedule",{"id":i,"name":x.name}); return {"id":i}

@router.put("/automation/schedules/{id}")
def update_schedule(id:str,x:ScheduleIn,_:str=Depends(auth)):
    if not legacy.one("select id from schedules where id=?",(id,)): raise HTTPException(404,"Schedule not found")
    seconds=float(x.expression) if x.kind=="interval" and str(x.expression).replace(".","",1).isdigit() else 3600
    legacy.execute("update schedules set name=?,kind=?,expression=?,action=?,payload=?,enabled=?,next_run=?,updated=? where id=?",(x.name,x.kind,x.expression,x.action,legacy.dumps(x.payload),int(x.enabled),legacy.now()+seconds,legacy.now(),id))
    return {"ok":True}

@router.delete("/automation/schedules/{id}")
def delete_schedule(id:str,_:str=Depends(auth)):
    legacy.execute("delete from schedules where id=?",(id,)); return {"ok":True}

@router.post("/automation/run-due")
async def run_due(_:str=Depends(auth)):
    due=legacy.rows("select * from schedules where enabled=1 and next_run<=? order by next_run",(legacy.now(),))
    results=[]
    for s in due:
        p=j(s["payload"])
        try:
            if s["action"]=="chat": result=await assistant_run(AssistantIn(message=str(p.get("message","")),require_approval=False))
            else: result={"status":"unsupported_action","action":s["action"]}
            ok=True
        except Exception as e: result={"error":str(e)}; ok=False
        interval=float(s["expression"]) if s["kind"]=="interval" and str(s["expression"]).replace(".","",1).isdigit() else 3600
        legacy.execute("update schedules set last_run=?,next_run=?,updated=? where id=?",(legacy.now(),legacy.now()+interval,legacy.now(),s["id"]))
        results.append({"id":s["id"],"ok":ok,"result":result})
    return results

@router.post("/files/upload")
async def upload_workspace(file:UploadFile=File(...),_:str=Depends(auth)):
    data=await file.read()
    if len(data)>25*1024*1024: raise HTTPException(413,"File too large")
    fid=str(uuid.uuid4()); safe=re.sub(r"[^A-Za-z0-9._-]","_",file.filename or "upload")
    path=STORE/f"{fid}_{safe}"; path.write_bytes(data)
    sha=hashlib.sha256(data).hexdigest(); t=legacy.now()
    legacy.execute("insert into workspace_files values(?,?,?,?,?,?,?,?)",(fid,safe,str(path.relative_to(ROOT)),file.content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream",len(data),sha,t,t))
    audit("file.upload","workspace_file",{"id":fid,"name":safe}); return {"id":fid,"name":safe,"size":len(data),"sha256":sha}

@router.get("/files")
def files(_:str=Depends(auth)): return legacy.rows("select * from workspace_files order by created desc")

@router.get("/files/{id}")
def file_info(id:str,_:str=Depends(auth)):
    x=legacy.one("select * from workspace_files where id=?",(id,))
    if not x: raise HTTPException(404,"File not found")
    return x

@router.delete("/files/{id}")
def delete_file(id:str,_:str=Depends(auth)):
    x=legacy.one("select * from workspace_files where id=?",(id,))
    if x:
        p=ROOT/x["path"]
        if p.exists(): p.unlink()
        legacy.execute("delete from workspace_files where id=?",(id,))
    return {"ok":True}

@router.get("/media")
def media(_:str=Depends(auth)): return legacy.rows("select * from media_jobs order by created desc")

@router.post("/media")
def create_media(x:MediaIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=legacy.now()
    legacy.execute("insert into media_jobs values(?,?,?,?,?,?,?,?)",(i,x.kind,"queued",legacy.dumps(x.input),None,x.provider,t,t))
    legacy.emit("media.queued",{"id":i,"kind":x.kind}); return {"id":i,"status":"queued"}

@router.get("/connectors")
def connectors(_:str=Depends(auth)):
    out=legacy.rows("select * from connectors order by name")
    for x in out: x["config"]=j(x["config"])
    return out

@router.post("/connectors")
def add_connector(x:ConnectorIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=legacy.now()
    legacy.execute("insert into connectors values(?,?,?,?,?,?,?,?)",(i,x.name,x.kind,x.base_url,int(x.enabled),legacy.dumps(x.config),t,t))
    audit("connector.create","connector",{"id":i,"kind":x.kind}); return {"id":i}

@router.put("/connectors/{id}")
def update_connector(id:str,x:ConnectorIn,_:str=Depends(auth)):
    legacy.execute("update connectors set name=?,kind=?,base_url=?,enabled=?,config=?,updated=? where id=?",(x.name,x.kind,x.base_url,int(x.enabled),legacy.dumps(x.config),legacy.now(),id)); return {"ok":True}

@router.delete("/connectors/{id}")
def delete_connector(id:str,_:str=Depends(auth)):
    legacy.execute("delete from connectors where id=?",(id,)); return {"ok":True}

@router.post("/diagnostics")
async def diagnostics(_:str=Depends(auth)):
    checks={"database":False,"provider_api":False,"tool_engine":False,"workflow_engine":False}
    try: legacy.rows("select 1"); checks["database"]=True
    except: pass
    checks["tool_engine"]=bool(legacy.one("select id from tools where id='core.echo'"))
    checks["workflow_engine"]=True
    for p in legacy.rows("select * from providers where enabled=1 and base_url<>'' limit 3"):
        try:
            async with legacy.httpx.AsyncClient(timeout=5) as c:
                r=await c.get(p["base_url"]); checks["provider_api"]=r.status_code<500
        except: pass
    return {"ok":all(checks.values()) if checks else False,"checks":checks,"timestamp":legacy.now()}

async def scheduler_loop():
    while True:
        try:
            due=legacy.rows("select * from schedules where enabled=1 and next_run<=? limit 20",(legacy.now(),))
            for s in due:
                p=j(s["payload"])
                if s["action"]=="chat" and p.get("message"):
                    await assistant_run(AssistantIn(message=p["message"]))
                interval=float(s["expression"]) if s["kind"]=="interval" and str(s["expression"]).replace(".","",1).isdigit() else 3600
                legacy.execute("update schedules set last_run=?,next_run=?,updated=? where id=?",(legacy.now(),legacy.now()+interval,legacy.now(),s["id"]))
        except Exception:
            pass
        await asyncio.sleep(15)


# ---- Extended intelligence adapters: document parsing, semantic memory, research, agents, GitHub ----
def _tokens(text):
    return set(re.findall(r"[A-Za-z0-9\u0900-\u097F]{3,}", (text or "").lower()))

def _semantic_score(query, text):
    q=_tokens(query); t=_tokens(text)
    if not q or not t: return 0.0
    return len(q&t)/max(1,len(q))

@router.get("/memory/semantic")
def semantic_memory(q:str="",limit:int=20,_:str=Depends(auth)):
    data=legacy.rows("select * from memories order by created desc limit 1000")
    ranked=sorted(data,key=lambda x:_semantic_score(q,x.get("content","")),reverse=True)
    return [{"id":x["id"],"scope":x["scope"],"content":x["content"],"metadata":j(x["metadata"]),"score":round(_semantic_score(q,x.get("content","")),4)} for x in ranked[:min(limit,100)]]

@router.post("/agents/{id}/run")
async def run_agent(id:str,payload:dict,_:str=Depends(auth)):
    agent=legacy.one("select * from agents where id=? and enabled=1",(id,))
    if not agent: raise HTTPException(404,"Agent not found or disabled")
    message=str(payload.get("message","")).strip()
    if not message: raise HTTPException(400,"message required")
    max_steps=min(int(payload.get("max_steps",5)),10)
    rid=str(uuid.uuid4()); trace("Understanding",rid,{"agent_id":id})
    history=[]; current=message
    for step in range(max_steps):
        intent=classify(current); trace("Planning",rid,{"step":step+1,"intent":intent})
        if intent=="tool":
            expr=re.sub(r"^(please\s+)?(calculate|compute)\s+","",current,flags=re.I)
            result=await legacy.execute_tool_internal("core.calculator",{"expression":expr})
            history.append({"step":step+1,"action":"calculator","result":result})
            trace("Verifying",rid,{"step":step+1})
            break
        answer,provider=await legacy.provider_chat(current,agent["model_id"])
        if answer is None:
            answer="Local agent result: "+current
            provider="local"
        history.append({"step":step+1,"action":"model","provider":provider,"answer":answer})
        break
    trace("Complete",rid,{"steps":len(history)})
    return {"request_id":rid,"agent_id":id,"state":"Complete","steps":history}

async def _extract_bytes(name,data,mime):
    ext=Path(name).suffix.lower()
    if ext in {".txt",".md",".csv",".json",".html",".htm",".xml",".log"} or mime.startswith("text/"):
        return data.decode("utf-8","ignore")
    if ext==".pdf":
        try:
            from pypdf import PdfReader
            import io
            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        except Exception as e:
            return "[PDF extraction unavailable: %s]"%e
    if ext==".docx":
        try:
            from docx import Document
            import io
            return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
        except Exception as e:
            return "[DOCX extraction unavailable: %s]"%e
    if ext==".xlsx":
        try:
            from openpyxl import load_workbook
            import io
            wb=load_workbook(io.BytesIO(data),read_only=True,data_only=True)
            out=[]
            for ws in wb.worksheets:
                out.append("## "+ws.title)
                for row in ws.iter_rows(values_only=True):
                    out.append(" | ".join("" if v is None else str(v) for v in row))
            return "\n".join(out)
        except Exception as e:
            return "[XLSX extraction unavailable: %s]"%e
    return ""

@router.get("/files/{id}/text")
def file_text(id:str,_:str=Depends(auth)):
    x=legacy.one("select * from workspace_files where id=?",(id,))
    if not x: raise HTTPException(404,"File not found")
    p=ROOT/x["path"]
    if not p.exists(): raise HTTPException(404,"File bytes missing")
    text=_extract_bytes(x["name"],p.read_bytes(),x["mime"])
    if hasattr(text,"__await__"): text=asyncio.run(text)
    return {"id":id,"name":x["name"],"text":text[:100000]}

@router.post("/files/{id}/index")
def index_file(id:str,_:str=Depends(auth)):
    x=legacy.one("select * from workspace_files where id=?",(id,))
    if not x: raise HTTPException(404,"File not found")
    p=ROOT/x["path"]
    if not p.exists(): raise HTTPException(404,"File bytes missing")
    raw=p.read_bytes()
    ext=Path(x["name"]).suffix.lower()
    text=""
    if ext in {".txt",".md",".csv",".json",".html",".htm",".xml",".log"} or x["mime"].startswith("text/"):
        text=raw.decode("utf-8","ignore")
    else:
        try:
            import io
            if ext==".pdf":
                from pypdf import PdfReader
                text="\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
            elif ext==".docx":
                from docx import Document
                text="\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
            elif ext==".xlsx":
                from openpyxl import load_workbook
                wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
                text="\n".join(" | ".join("" if v is None else str(v) for v in row) for ws in wb.worksheets for row in ws.iter_rows(values_only=True))
        except Exception as e: text=""
    memory_id=str(uuid.uuid4()); t=legacy.now()
    legacy.execute("insert into memories values(?,?,?,?,?)",(memory_id,"file:"+id,text[:100000],legacy.dumps({"file_id":id,"name":x["name"]}),t))
    legacy.execute("insert or replace into memory_index values(?,?,?,?,?)",(str(uuid.uuid4()),memory_id,legacy.dumps(list(_tokens(text))),hashlib.sha256(text.encode()).hexdigest(),t))
    return {"ok":True,"memory_id":memory_id,"characters":len(text)}

@router.get("/research/search")
async def web_search(q:str,limit:int=5,_:str=Depends(auth)):
    if not q.strip(): raise HTTPException(400,"q required")
    url="https://html.duckduckgo.com/html/"
    try:
        async with legacy.httpx.AsyncClient(timeout=15,headers={"User-Agent":"RAYONE-AI/2.0"}) as c:
            r=await c.get(url,params={"q":q})
            body=r.text
        hits=[]
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',body,re.S|re.I):
            href=re.sub("<.*?>","",m.group(1)); title=re.sub("<.*?>","",m.group(2))
            hits.append({"title":title,"url":href})
            if len(hits)>=min(limit,10): break
        metric("research.search")
        return {"query":q,"results":hits,"source":"duckduckgo-html"}
    except Exception as e:
        return {"query":q,"results":[],"error":str(e)}

def _gh_headers():
    h={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2026-03-10"}
    tok=os.getenv("GITHUB_TOKEN","")
    if tok: h["Authorization"]="Bearer "+tok
    return h

def _gh_allowed(path):
    return bool(re.match(r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^?]*)?$",path))

@router.get("/github/status")
async def github_status(_:str=Depends(auth)):
    tok=bool(os.getenv("GITHUB_TOKEN"))
    if not tok: return {"configured":False,"message":"Set GITHUB_TOKEN to enable authenticated GitHub control."}
    try:
        async with legacy.httpx.AsyncClient(timeout=10) as c:
            r=await c.get("https://api.github.com/user",headers=_gh_headers())
            return {"configured":True,"ok":r.status_code==200,"status":r.status_code,"user":r.json().get("login") if r.status_code==200 else None}
    except Exception as e: return {"configured":True,"ok":False,"error":str(e)}

@router.post("/github/request")
async def github_request(payload:dict,_:str=Depends(auth)):
    method=str(payload.get("method","GET")).upper()
    path=str(payload.get("path",""))
    if method not in {"GET","POST","PATCH","DELETE"} or not _gh_allowed(path):
        raise HTTPException(400,"Only GitHub repository API paths are allowed")
    if method!="GET":
        aid=str(uuid.uuid4()); t=legacy.now()
        legacy.execute("insert into approvals values(?,?,?,?,?,?,?)",(aid,"github."+method,path,legacy.dumps(payload),"pending",t,None))
        return {"status":"Awaiting Approval","approval_id":aid}
    tok=os.getenv("GITHUB_TOKEN")
    if not tok: raise HTTPException(503,"GITHUB_TOKEN is not configured")
    try:
        async with legacy.httpx.AsyncClient(timeout=20) as c:
            r=await c.get("https://api.github.com"+path,headers=_gh_headers())
            return {"status":r.status_code,"data":r.json() if "application/json" in r.headers.get("content-type","") else r.text[:20000]}
    except Exception as e: raise HTTPException(502,str(e))
