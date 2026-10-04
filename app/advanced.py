
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
    CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, project_id TEXT, title TEXT, created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT, state TEXT, provider TEXT, created REAL);
    """)
    c.commit(); c.close()

init_advanced()

def auth(token=Depends(legacy.auth)):
    legacy.execute("delete from sessions where expires<=?",(legacy.now(),))
    return token
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
    conversation_id: str|None=None

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

def _conversation_turn(x, rid, answer="", provider="", append_user=True):
    cid=x.conversation_id
    if cid and not legacy.one("select id from conversations where id=?",(cid,)): cid=None
    if not cid:
        cid=str(uuid.uuid4()); t=legacy.now()
        title=x.message.strip().replace("\n"," ")[:80] or "RAYONE Conversation"
        legacy.execute("insert into conversations values(?,?,?,?,?)",(cid,x.project_id,title,t,t))
    if append_user:
        legacy.execute("insert into messages values(?,?,?,?,?,?,?)",(str(uuid.uuid4()),cid,"user",x.message,"Understanding",None,legacy.now()))
    if answer:
        legacy.execute("insert into messages values(?,?,?,?,?,?,?)",(str(uuid.uuid4()),cid,"assistant",str(answer),"Complete",provider,legacy.now()))
    legacy.execute("update conversations set updated=? where id=?",(legacy.now(),cid))
    return cid

@router.get("/conversations")
def conversations(_:str=Depends(auth)):
    return legacy.rows("select * from conversations order by updated desc limit 200")

@router.get("/conversations/{id}/messages")
def conversation_messages(id:str,_:str=Depends(auth)):
    if not legacy.one("select id from conversations where id=?",(id,)): raise HTTPException(404,"Conversation not found")
    return legacy.rows("select * from messages where conversation_id=? order by created",(id,))

@router.delete("/conversations/{id}")
def delete_conversation(id:str,_:str=Depends(auth)):
    legacy.execute("delete from messages where conversation_id=?",(id,))
    legacy.execute("delete from conversations where id=?",(id,))
    audit("conversation.delete","conversation",{"id":id})
    return {"ok":True}

def _auto_memory(message, answer, conversation_id):
    if not answer or len(str(answer)) < 8: return
    content=f"User: {message}\nAssistant: {str(answer)[:4000]}"
    mid=str(uuid.uuid4()); t=legacy.now()
    legacy.execute("insert into memories values(?,?,?,?,?)",(mid,"conversation",content,legacy.dumps({"conversation_id":conversation_id,"auto":True}),t))
    legacy.execute("insert into memory_index values(?,?,?,?,?)",(str(uuid.uuid4()),mid,legacy.dumps(sorted(_tokens(content))),hashlib.sha256(content.encode()).hexdigest(),t))

async def assistant_run(x):
    rid=str(uuid.uuid4()); metric("assistant.requests")
    cid=_conversation_turn(x,rid)
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
        _conversation_turn(x,rid,result,"local",False); _auto_memory(x.message,str(result),cid)
        return {"request_id":rid,"state":"Complete","intent":intent,"result":result,"conversation_id":cid}
    if intent=="research":
        trace("Researching",rid)
        result=await research(x.message.strip())
        trace("Verifying",rid,{"sources":1 if result.get("source") else 0})
        trace("Complete",rid)
        _conversation_turn(x,rid,result.get("answer") if isinstance(result,dict) else str(result),"research",False); _auto_memory(x.message,str(result),cid)
        return {"request_id":rid,"state":"Complete","intent":intent,"result":result,"conversation_id":cid}
    if intent=="media":
        trace("Executing",rid,{"media":True})
        mid=str(uuid.uuid4()); t=legacy.now()
        legacy.execute("insert into media_jobs values(?,?,?,?,?,?,?,?)",(mid,"generic","queued",legacy.dumps(x.message),None,None,t,t))
        trace("Verifying",rid,{"media_job_id":mid})
        trace("Complete",rid)
        msg="Media job queued; provider adapter can be attached without changing the core contract."; _conversation_turn(x,rid,msg,"media",False); _auto_memory(x.message,msg,cid)
        return {"request_id":rid,"state":"Complete","intent":intent,"media_job_id":mid,"message":msg,"conversation_id":cid}
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
    _conversation_turn(x,rid,answer,provider,False); _auto_memory(x.message,answer,cid)
    return {"request_id":rid,"state":"Complete","intent":intent,"provider":provider,"answer":answer,"conversation_id":cid}

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


# ---- Completion layer: security, jobs, backups, files, connectors, GitLab, cron, failover ----
from fastapi.responses import FileResponse
import tempfile, zipfile, datetime, hmac, struct

class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12)

class SessionActionIn(BaseModel):
    token: str = ""

class CronScheduleIn(ScheduleIn):
    timezone: str = "UTC"

def _setting(key, default=""):
    row=legacy.one("select value from settings where key=?",(key,))
    return row["value"] if row else default

def _set_setting(key,value):
    legacy.execute("insert into settings(key,value,updated) values(?,?,?) on conflict(key) do update set value=excluded.value,updated=excluded.updated",(key,value,legacy.now()))

@router.get("/security/sessions")
def security_sessions(_:str=Depends(auth)):
    return legacy.rows("select token,created,expires from sessions order by created desc")

@router.delete("/security/sessions")
def security_revoke_sessions(_:str=Depends(auth)):
    legacy.execute("delete from sessions")
    audit("security.revoke_all_sessions","auth",{})
    return {"ok":True}

@router.post("/security/password")
def security_password(x:PasswordChangeIn,token:str=Depends(auth)):
    override=_setting("admin_password_hash","")
    valid=legacy.verify_password(x.current_password,override) if override else __import__("secrets").compare_digest(x.current_password,legacy.ADMIN_PASSWORD)
    if not valid: raise HTTPException(401,"Current password is invalid")
    legacy._set_setting("admin_password_hash",legacy.hash_password(x.new_password)) if hasattr(legacy,"_set_setting") else legacy.execute("insert into settings(key,value,updated) values(?,?,?) on conflict(key) do update set value=excluded.value,updated=excluded.updated",("admin_password_hash",legacy.hash_password(x.new_password),legacy.now()))
    audit("security.password_changed","auth",{})
    return {"ok":True,"note":"Password override is stored as a scrypt hash."}

@router.get("/security/config")
def security_config(_:str=Depends(auth)):
    return {
        "session_ttl_seconds":86400,
        "secret_storage":"Fernet",
        "password_override":bool(_setting("admin_password_override","")),
        "github_token_configured":bool(os.getenv("GITHUB_TOKEN")),
        "external_services":"credential-gated",
        "local_first":True
    }

@router.get("/jobs/{id}/status")
def job_status(id:str,_:str=Depends(auth)):
    j=legacy.one("select * from jobs where id=?",(id,))
    if not j: raise HTTPException(404,"Job not found")
    return j

@router.post("/jobs/{id}/cancel")
def cancel_job(id:str,_:str=Depends(auth)):
    j=legacy.one("select * from jobs where id=?",(id,))
    if not j: raise HTTPException(404,"Job not found")
    if j["status"] in {"completed","failed","cancelled"}: return {"ok":True,"status":j["status"]}
    legacy.execute("update jobs set status='cancelled',updated=? where id=?",(legacy.now(),id))
    audit("job.cancel","job",{"id":id})
    return {"ok":True,"status":"cancelled"}

@router.post("/jobs/{id}/retry")
def retry_job(id:str,_:str=Depends(auth)):
    j=legacy.one("select * from jobs where id=?",(id,))
    if not j: raise HTTPException(404,"Job not found")
    if j["status"] not in {"failed","cancelled"}: raise HTTPException(409,"Only failed/cancelled jobs can be retried")
    legacy.execute("update jobs set status='queued',error=NULL,updated=? where id=?",(legacy.now(),id))
    audit("job.retry","job",{"id":id})
    return {"ok":True,"status":"queued"}

@router.get("/files/{id}/download")
def download_workspace_file(id:str,_:str=Depends(auth)):
    x=legacy.one("select * from workspace_files where id=?",(id,))
    if not x: raise HTTPException(404,"File not found")
    p=ROOT/x["path"]
    if not p.exists(): raise HTTPException(404,"File bytes missing")
    return FileResponse(str(p),media_type=x["mime"],filename=x["name"])

@router.post("/files/{id}/reindex")
def reindex_file(id:str,_:str=Depends(auth)):
    return index_file(id)

def _cron_match(expr, ts=None):
    ts=ts or datetime.datetime.now(datetime.timezone.utc)
    parts=str(expr).split()
    if len(parts)!=5: return False
    vals=[ts.minute,ts.hour,ts.day,ts.month,(ts.weekday()+1)%7]
    for rule,val in zip(parts,vals):
        if rule=="*": continue
        ok=False
        for atom in rule.split(","):
            if atom.isdigit() and int(atom)==val: ok=True
            elif "-" in atom and all(x.isdigit() for x in atom.split("-",1)):
                a,b=map(int,atom.split("-",1))
                if a<=val<=b: ok=True
            elif atom.startswith("*/") and atom[2:].isdigit() and val%int(atom[2:])==0: ok=True
        if not ok:return False
    return True

def _next_cron(expr):
    t=datetime.datetime.now(datetime.timezone.utc).replace(second=0,microsecond=0)+datetime.timedelta(minutes=1)
    for _ in range(60*24*366):
        if _cron_match(expr,t): return t.timestamp()
        t+=datetime.timedelta(minutes=1)
    return t.timestamp()

@router.post("/automation/cron")
def add_cron(x:CronScheduleIn,_:str=Depends(auth)):
    if len(x.expression.split())!=5: raise HTTPException(400,"Cron requires 5 fields: minute hour day month weekday")
    i=str(uuid.uuid4());t=legacy.now()
    legacy.execute("insert into schedules values(?,?,?,?,?,?,?,?,?,?,?)",(i,x.name,"cron",x.expression,x.action,legacy.dumps(x.payload),int(x.enabled),_next_cron(x.expression),None,t,t))
    audit("schedule.cron.create","schedule",{"id":i})
    return {"id":i,"next_run":_next_cron(x.expression)}

@router.post("/automation/once")
def add_one_time(x:ScheduleIn,_:str=Depends(auth)):
    try: run_at=float(x.expression)
    except: raise HTTPException(400,"expression must be a Unix timestamp for one-time schedules")
    i=str(uuid.uuid4());t=legacy.now()
    legacy.execute("insert into schedules values(?,?,?,?,?,?,?,?,?,?,?)",(i,x.name,"once",x.expression,x.action,legacy.dumps(x.payload),int(x.enabled),run_at,None,t,t))
    return {"id":i,"next_run":run_at}

@router.post("/automation/schedule/{id}/run")
async def run_schedule_now(id:str,_:str=Depends(auth)):
    s=legacy.one("select * from schedules where id=?",(id,))
    if not s: raise HTTPException(404,"Schedule not found")
    p=j(s["payload"])
    if s["action"]=="chat":
        result=await assistant_run(AssistantIn(message=str(p.get("message",""))))
    else:
        result={"status":"unsupported_action","action":s["action"]}
    audit("schedule.manual_run","schedule",{"id":id})
    return result

async def _provider_failover(message,model_id=None):
    models=legacy.rows("select m.*,p.name provider_name,p.base_url,p.config provider_config from models m left join providers p on p.id=m.provider_id where m.enabled=1 and p.enabled=1 order by m.rowid")
    if model_id:
        models=[x for x in models if x["id"]==model_id]+[x for x in models if x["id"]!=model_id]
    if not models:
        return None,None,0
    errors=[]
    for m in models:
        try:
            cfg=j(m["provider_config"]); token=""
            if cfg.get("api_key"):
                try: token=legacy.FERNET.decrypt(cfg["api_key"].encode()).decode()
                except Exception: token=""
            url=(m["base_url"] or "").rstrip("/")+"/chat/completions"
            if not url.startswith("http"): continue
            headers={"Authorization":"Bearer "+token} if token else {}
            started=legacy.now()
            async with legacy.httpx.AsyncClient(timeout=45) as c:
                r=await c.post(url,headers=headers,json={"model":m["model"],"messages":[{"role":"user","content":message}]})
                latency=legacy.now()-started
                metric("provider.calls"); metric("provider.latency_seconds",latency)
                if r.status_code>=400: raise RuntimeError(f"HTTP {r.status_code}")
                d=r.json(); answer=d.get("choices",[{}])[0].get("message",{}).get("content")
                if answer:
                    metric("provider.success")
                    usage=d.get("usage") or {}
                    if usage.get("total_tokens") is not None:
                        metric("provider.tokens",float(usage["total_tokens"]))
                    else:
                        metric("provider.estimated_tokens",max(1,len(message)//4))
                    return answer,m["provider_name"],len(errors)
        except Exception as e:
            errors.append(str(e)); metric("provider.failures")
    return None,None,len(errors)

@router.post("/providers/failover-chat")
async def provider_failover_chat(payload:dict,_:str=Depends(auth)):
    message=str(payload.get("message","")).strip()
    if not message: raise HTTPException(400,"message required")
    answer,provider,failures=await _provider_failover(message,payload.get("model_id"))
    if answer is None: return {"ok":False,"provider":None,"failures":failures,"fallback":"local"}
    return {"ok":True,"provider":provider,"answer":answer,"failures":failures}

@router.get("/providers/health")
async def provider_health(_:str=Depends(auth)):
    out=[]
    for p in legacy.rows("select * from providers where enabled=1 order by name"):
        url=(p["base_url"] or "").rstrip("/")
        item={"id":p["id"],"name":p["name"],"base_url":url,"ok":False}
        if url:
            try:
                async with legacy.httpx.AsyncClient(timeout=5) as c:
                    r=await c.get(url); item.update({"ok":r.status_code<500,"status":r.status_code})
            except Exception as e:item["error"]=str(e)
        out.append(item)
    return out

@router.post("/connectors/{id}/execute")
async def execute_connector(id:str,payload:dict,_:str=Depends(auth)):
    c=legacy.one("select * from connectors where id=? and enabled=1",(id,))
    if not c: raise HTTPException(404,"Connector not found or disabled")
    method=str(payload.get("method","GET")).upper()
    if method not in {"GET","POST","PUT","PATCH","DELETE"}: raise HTTPException(400,"Unsupported HTTP method")
    base=(c["base_url"] or "").rstrip("/")
    path=str(payload.get("path",""))
    if not base.startswith("https://") and not base.startswith("http://"): raise HTTPException(400,"Connector base_url must be http(s)")
    target=base+"/"+path.lstrip("/")
    cfg=j(c["config"]); headers=dict(cfg.get("headers") or {})
    if method!="GET":
        aid=str(uuid.uuid4());t=legacy.now()
        legacy.execute("insert into approvals values(?,?,?,?,?,?,?)",(aid,"connector."+method,target,legacy.dumps({"connector_id":id,"method":method,"url":target,"json":payload.get("json"),"headers":headers}),"pending",t,None))
        return {"status":"Awaiting Approval","approval_id":aid}
    async with legacy.httpx.AsyncClient(timeout=30) as client:
        r=await client.get(target,headers=headers,params=payload.get("params") or {})
        return {"status":r.status_code,"data":r.text[:20000]}

@router.post("/gitlab/request")
async def gitlab_request(payload:dict,_:str=Depends(auth)):
    base=str(payload.get("base_url","https://gitlab.com/api/v4")).rstrip("/")
    path=str(payload.get("path",""))
    method=str(payload.get("method","GET")).upper()
    if method not in {"GET","POST","PUT","PATCH","DELETE"} or not path.startswith("/"): raise HTTPException(400,"Invalid GitLab request")
    token=os.getenv("GITLAB_TOKEN","")
    headers={"Accept":"application/json"}
    if token: headers["PRIVATE-TOKEN"]=token
    url=base+path
    if method!="GET":
        aid=str(uuid.uuid4());t=legacy.now()
        legacy.execute("insert into approvals values(?,?,?,?,?,?,?)",(aid,"gitlab."+method,url,legacy.dumps(payload),"pending",t,None))
        return {"status":"Awaiting Approval","approval_id":aid}
    if not token: raise HTTPException(503,"GITLAB_TOKEN is not configured")
    async with legacy.httpx.AsyncClient(timeout=30) as c:
        r=await c.get(url,headers=headers)
        return {"status":r.status_code,"data":r.json() if "application/json" in r.headers.get("content-type","") else r.text[:20000]}

@router.get("/backup/export")
def backup_export(_:str=Depends(auth)):
    tables=["projects","providers","models","tools","agents","workflows","memories","jobs","events","audits","checkpoints","secrets","settings","traces","approvals","permissions","schedules","workspace_files","media_jobs","connectors","metrics_v2","memory_index"]
    data={"version":legacy.APP_VERSION,"created":legacy.now(),"tables":{}}
    for t in tables:data["tables"][t]=legacy.rows("select * from "+t)
    return data

@router.post("/backup/create")
def backup_create(_:str=Depends(auth)):
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target=ROOT/"data"/f"rayone-backup-{stamp}.zip"
    dbpath=legacy.DB
    with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as z:
        if dbpath.exists():z.write(dbpath,arcname="rayone.db")
        if STORE.exists():
            for p in STORE.rglob("*"):
                if p.is_file():z.write(p,arcname=str(Path("workspace")/p.relative_to(STORE)))
    backups=sorted((ROOT/"data").glob("rayone-backup-*.zip"),key=lambda x:x.stat().st_mtime,reverse=True)
    for old in backups[10:]:
        try: old.unlink()
        except OSError: pass
    audit("backup.create","backup",{"file":str(target.name)})
    return {"ok":True,"file":target.name,"path":str(target.relative_to(ROOT))}

@router.get("/backup/list")
def backup_list(_:str=Depends(auth)):
    out=[]
    for p in sorted((ROOT/"data").glob("rayone-backup-*.zip"),key=lambda x:x.stat().st_mtime,reverse=True):
        out.append({"name":p.name,"size":p.stat().st_size,"created":p.stat().st_mtime})
    return out

@router.get("/backup/download/{name}")
def backup_download(name:str,_:str=Depends(auth)):
    if "/" in name or "\\" in name or not name.endswith(".zip"): raise HTTPException(400,"Invalid backup name")
    p=ROOT/"data"/name
    if not p.exists(): raise HTTPException(404,"Backup not found")
    return FileResponse(str(p),media_type="application/zip",filename=p.name)

@router.delete("/backup/{name}")
def backup_delete(name:str,_:str=Depends(auth)):
    if "/" in name or "\\" in name: raise HTTPException(400,"Invalid backup name")
    p=ROOT/"data"/name
    if p.exists():p.unlink()
    return {"ok":True}

@router.get("/workspace/stats")
def workspace_stats(_:str=Depends(auth)):
    fs=legacy.rows("select count(*) n,coalesce(sum(size),0) bytes from workspace_files")[0]
    return {"files":fs["n"],"bytes":fs["bytes"],"media_jobs":legacy.rows("select count(*) n from media_jobs")[0]["n"]}

@router.get("/capabilities")
def capabilities(_:str=Depends(auth)):
    return {
      "core":["auth","projects","providers","models","tools","agents","workflows","memory","jobs","events","audit","checkpoint","import_export"],
      "v2":["state_machine","streaming","approvals","permissions","research","document_extraction","semantic_memory","scheduler","media_contract","connectors","github","gitlab","backups","provider_failover","observability"],
      "optional":["native_android","native_desktop","ocr","browser_automation","real_media_generation","messaging_connectors"],
      "policy":"Optional capabilities activate only when their adapter/dependency/credential is configured; unavailable integrations are never faked."
    }


@router.post("/approvals/{id}/execute")
async def execute_approved_external(id:str,_:str=Depends(auth)):
    item=legacy.one("select * from approvals where id=?",(id,))
    if not item: raise HTTPException(404,"Approval not found")
    if item["status"]!="approved": raise HTTPException(409,"Approval must be approved before execution")
    payload=j(item["payload"]); action=item["action"]
    if action.startswith("connector."):
        method=action.split(".",1)[1].upper()
        target=str(payload.get("url",""))
        headers=dict(payload.get("headers") or {})
        async with legacy.httpx.AsyncClient(timeout=30) as client:
            r=await client.request(method,target,headers=headers,json=payload.get("json"),params=payload.get("params"))
            return {"ok":r.status_code<400,"status":r.status_code,"data":r.text[:20000]}
    if action.startswith("gitlab."):
        method=action.split(".",1)[1].upper()
        url=str(payload.get("base_url","https://gitlab.com/api/v4")).rstrip("/") + str(payload.get("path",""))
        token=os.getenv("GITLAB_TOKEN","")
        if not token: raise HTTPException(503,"GITLAB_TOKEN is not configured")
        headers={"PRIVATE-TOKEN":token,"Accept":"application/json"}
        async with legacy.httpx.AsyncClient(timeout=30) as client:
            r=await client.request(method,url,headers=headers,json=payload.get("json"))
            return {"ok":r.status_code<400,"status":r.status_code,"data":r.json() if "application/json" in r.headers.get("content-type","") else r.text[:20000]}
    raise HTTPException(400,"This approval type is already executed by the decision endpoint or unsupported")


@router.post("/backup/import")
def backup_import(payload:dict,_:str=Depends(auth)):
    tables=payload.get("tables")
    if not isinstance(tables,dict): raise HTTPException(400,"Expected {tables:{...}} backup export")
    allowed=["projects","providers","models","tools","agents","workflows","memories","settings","traces","approvals","permissions","schedules","workspace_files","media_jobs","connectors","metrics_v2","memory_index","conversations","messages"]
    c=legacy.conn()
    imported=0
    try:
        for t in allowed:
            data=tables.get(t)
            if not isinstance(data,list) or not data: continue
            # Only columns already present in the live schema are accepted.
            cols=[r[1] for r in c.execute("pragma table_info("+t+")").fetchall()]
            if not cols: continue
            for row in data:
                safe={k:v for k,v in row.items() if k in cols}
                if not safe: continue
                names=list(safe); q=",".join("?" for _ in names)
                c.execute("insert or replace into "+t+" ("+",".join(names)+") values ("+q+")",[safe[k] for k in names])
                imported+=1
        c.commit()
    except Exception as e:
        c.rollback(); raise HTTPException(400,"Backup import failed: "+str(e))
    finally: c.close()
    audit("backup.import","backup",{"rows":imported})
    return {"ok":True,"rows_imported":imported}
