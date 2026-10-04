
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
from pathlib import Path
import asyncio, base64, hashlib, json, mimetypes, os, re, time, uuid, datetime, zipfile
from . import main as legacy
from .local_tools import TOTAL_CAPABILITIES
from .local_adapters import browser_fetch, browser_fetch_text, extract_text, ocr_image, media_probe

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

def permission_allows(subject, capability, scope="global"):
    rows=legacy.rows("select effect,scope from permissions where subject=? and capability=?",(subject,capability))
    if any(r["effect"]=="deny" and (r["scope"] in {"global",scope}) for r in rows): return False
    return True

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
    request_id: str|None=None

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
def _verify_result(result, expected=None):
    ok=result is not None
    if isinstance(result,dict):
        if result.get("status") in {"failed","error"}: ok=False
        if result.get("native") and result.get("size") is not None and int(result.get("size",0))<=0: ok=False
        if result.get("verified") is False: ok=False
    if expected is not None and ok: ok=(result==expected)
    return {"ok":bool(ok),"expected":expected,"result_present":result is not None}

async def decide_approval(id:str,x:ApprovalIn,_:str=Depends(auth)):
    if x.decision not in {"approved","rejected"}: raise HTTPException(400,"decision must be approved or rejected")
    item=legacy.one("select * from approvals where id=?",(id,))
    if not item: raise HTTPException(404,"Approval not found")
    if item["status"]!="pending": raise HTTPException(409,"Approval already decided")
    result=None
    if x.decision=="approved" and item["action"]=="pipeline.execute":
        from .execution_pipeline import execute_approved
        result=await execute_approved(j(item["payload"]))
    elif x.decision=="approved" and item["action"].startswith("github."):
        payload=j(item["payload"]); method=item["action"].split(".",1)[1]
        path=str(payload.get("path",""))
        if not _gh_allowed(path): raise HTTPException(400,"GitHub path not allowed")
        if not os.getenv("GITHUB_TOKEN"): raise HTTPException(503,"GITHUB_TOKEN is not configured")
        headers=_gh_headers(); body=payload.get("body") or {}
        async with legacy.httpx.AsyncClient(timeout=30) as c:
            rr=await c.request(method,"https://api.github.com"+path,headers=headers,json=body)
            result={"status":rr.status_code,"data":rr.json() if "application/json" in rr.headers.get("content-type","") else rr.text[:20000]}
            if rr.status_code>=400: raise HTTPException(rr.status_code,"GitHub request failed: "+rr.text[:2000])
    elif x.decision=="approved" and item["action"]=="vorqyon.execute":
        payload=j(item["payload"]); mode=str(payload.get("mode","")); target=str(payload.get("target","")); args=payload.get("args") or {}
        if mode=="tool": result=await legacy.execute_tool_internal(target,args)
        elif mode=="workflow": result=await legacy.run_workflow_internal(target,args)
        elif mode=="chat":
            answer,provider=await legacy.provider_chat(target,args.get("model_id"))
            result={"answer":answer or ("RAYONE local core received: "+target),"provider":provider or "local"}
        else: raise HTTPException(400,"Unsupported approved VORQYON mode")
        verification=_verify_result(result,payload.get("expected"))
        result={"execution":result,"verification":verification}
        audit("vorqyon.approved_execute","execution",{"approval_id":id,"verified":verification["ok"]})
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
    from .native_engines import research as native_research
    return native_research(url_or_query, max_sources=5)
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
    from .execution_pipeline import run_pipeline
    result=await run_pipeline(message=x.message,model_id=x.model_id,conversation_id=x.conversation_id,
                              require_approval=x.require_approval,request_id=getattr(x,"request_id",None))
    cid=x.conversation_id
    if result.get("state")=="Complete":
        answer=result.get("answer") or result.get("message") or result.get("result")
        if answer is not None:
            try:
                cid=cid or _conversation_turn(x,result["request_id"],"", "", True)
                _conversation_turn(x,result["request_id"],answer,result.get("provider","local"),False)
                _auto_memory(x.message,str(answer),cid)
                result["conversation_id"]=cid
            except Exception:
                pass
    return result

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

def _advance_schedule(s, finished_at=None):
    now_ts=finished_at or legacy.now()
    kind=s["kind"]
    if kind=="once":
        return None
    if kind=="cron":
        return _next_cron(s["expression"])
    if kind=="interval":
        try:
            return now_ts + max(1.0,float(s["expression"]))
        except Exception:
            return now_ts + 3600.0
    return now_ts + 3600.0

async def _execute_schedule(s):
    p=j(s["payload"])
    action=s["action"]
    if action=="chat":
        return await assistant_run(AssistantIn(message=str(p.get("message","")),require_approval=False))
    if action=="tool":
        name=str(p.get("name","")).strip()
        if not name: raise ValueError("scheduled tool requires payload.name")
        return await legacy.execute_tool_internal(name,p.get("args") or {})
    if action=="workflow":
        wid=str(p.get("workflow_id","")).strip()
        if not wid: raise ValueError("scheduled workflow requires payload.workflow_id")
        return await legacy.run_workflow_internal(wid,p.get("input") or {})
    raise ValueError("unsupported scheduled action: "+action)

@router.post("/automation/run-due")
async def run_due(_:str=Depends(auth)):
    due=legacy.rows("select * from schedules where enabled=1 and next_run<=? order by next_run",(legacy.now(),))
    results=[]
    for s in due:
        try:
            result=await _execute_schedule(s)
            ok=True
        except Exception as e:
            result={"error":str(e)}
            ok=False
        finished=legacy.now()
        next_run=_advance_schedule(s,finished)
        enabled=0 if s["kind"]=="once" else int(s["enabled"])
        legacy.execute("update schedules set last_run=?,next_run=?,enabled=?,updated=? where id=?",(finished,next_run,enabled,finished,s["id"]))
        audit("schedule.executed","schedule",{"id":s["id"],"ok":ok,"next_run":next_run})
        results.append({"id":s["id"],"ok":ok,"result":result,"next_run":next_run,"enabled":bool(enabled)})
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
async def create_media(x:MediaIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=legacy.now()
    legacy.execute("insert into media_jobs values(?,?,?,?,?,?,?,?)",(i,x.kind,"queued",legacy.dumps(x.input),None,x.provider,t,t))
    # Native-first execution: media creation is immediately executable without a provider.
    native_kinds={"image","design","audio","music","voice","video"}
    if x.kind in native_kinds and (not x.provider or x.provider=="native"):
        try:
            result=await legacy.execute_tool_internal(f"local.{x.kind}.generate",x.input)
            legacy.execute("update media_jobs set status=?,output=?,provider=?,updated=? where id=?",("completed",legacy.dumps(result),"native",legacy.now(),i))
            legacy.emit("media.completed",{"id":i,"kind":x.kind,"native":True})
            return {"id":i,"status":"completed","provider":"native","result":result}
        except Exception as e:
            legacy.execute("update media_jobs set status=?,output=?,provider=?,updated=? where id=?",("failed",legacy.dumps({"error":str(e)}),"native",legacy.now(),i))
            legacy.emit("media.failed",{"id":i,"kind":x.kind,"error":str(e)})
            raise HTTPException(500,"Native media generation failed: "+str(e))
    legacy.emit("media.queued",{"id":i,"kind":x.kind}); return {"id":i,"status":"queued","provider":x.provider or "external-adapter"}

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
                try:
                    await _execute_schedule(s)
                except Exception as e:
                    audit("schedule.error","schedule",{"id":s["id"],"error":str(e)})
                finished=legacy.now()
                next_run=_advance_schedule(s,finished)
                enabled=0 if s["kind"]=="once" else int(s["enabled"])
                legacy.execute("update schedules set last_run=?,next_run=?,enabled=?,updated=? where id=?",(finished,next_run,enabled,finished,s["id"]))
        except Exception as e:
            audit("scheduler.loop_error","scheduler",{"error":str(e)})
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
    max_steps=max(1,min(int(payload.get("max_steps",5)),10))
    plan=payload.get("steps") or [{"type":"chat","message":message}]
    if not isinstance(plan,list) or not plan: raise HTTPException(400,"steps must be a non-empty list")
    from .execution_pipeline import run_pipeline
    rid=str(uuid.uuid4()); history=[]
    for step_no,step in enumerate(plan[:max_steps],1):
        if not isinstance(step,dict): raise HTTPException(400,f"step {step_no}: expected object")
        kind=step.get("type",step.get("action",""))
        if kind=="tool":
            name=str(step.get("name","")).strip()
            if not name: raise HTTPException(400,f"step {step_no}: tool name required")
            result=await run_pipeline(message="",kind="tool",target=name,args=step.get("args") or {},request_id=f"{rid}:{step_no}")
            history.append({"step":step_no,"action":"tool","tool":name,"result":result})
        elif kind=="chat":
            prompt=str(step.get("message",message))
            result=await run_pipeline(message=prompt,model_id=agent["model_id"],request_id=f"{rid}:{step_no}")
            history.append({"step":step_no,"action":"chat","result":result})
        elif kind=="memory":
            content=str(step.get("content",message))
            mid=str(uuid.uuid4()); t=legacy.now()
            legacy.execute("insert into memories values(?,?,?,?,?)",(mid,step.get("scope","agent"),content,legacy.dumps(step.get("metadata",{})),t))
            history.append({"step":step_no,"action":"memory","memory_id":mid,"content":content})
        elif kind=="workflow":
            wid=str(step.get("workflow_id","")).strip()
            if not wid: raise HTTPException(400,f"step {step_no}: workflow_id required")
            result=await run_pipeline(message="",kind="workflow",target=wid,args=step.get("input") or {},request_id=f"{rid}:{step_no}")
            history.append({"step":step_no,"action":"workflow","workflow_id":wid,"result":result})
        else:
            raise HTTPException(400,f"step {step_no}: unsupported action '{kind}'")
    return {"request_id":rid,"agent_id":id,"state":"Complete","steps":history,"step_count":len(history)}


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

@router.post("/local/browser/fetch")
def local_browser_fetch(payload:dict,_:str=Depends(auth)):
    url=str(payload.get("url","")).strip()
    if not url: raise HTTPException(400,"url is required")
    try:
        r=browser_fetch(url,int(payload.get("max_bytes",200000)))
        r["text"]=extract_text(r["content"]) if "html" in r["content_type"].lower() else r["content"]
        return r
    except Exception as e: raise HTTPException(400,"Browser fetch failed: "+str(e))

@router.post("/local/ocr")
def local_ocr(payload:dict,_:str=Depends(auth)):
    file_id=str(payload.get("file_id",""))
    row=legacy.one("select path from workspace_files where id=?",(file_id,))
    if not row: raise HTTPException(404,"File not found")
    result=ocr_image(row["path"])
    if not result.get("available",True): raise HTTPException(503,result.get("reason","OCR engine unavailable"))
    return result

@router.post("/local/media/probe")
def local_media_probe(payload:dict,_:str=Depends(auth)):
    file_id=str(payload.get("file_id",""))
    row=legacy.one("select path from workspace_files where id=?",(file_id,))
    if not row: raise HTTPException(404,"File not found")
    result = media_probe(row["path"])
    result["adapter"] = "local-media-probe"
    return result

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
    result=await _execute_schedule(s)
    audit("schedule.manual_run","schedule",{"id":id,"kind":s["kind"]})
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
                    rr=await c.get(url)
                    item["ok"]=rr.status_code < 500
                    item["status"]=rr.status_code
            except Exception as ex:
                item["error"]=str(ex)
        out.append(item)
    return {"providers":out}

# Unified execution control endpoints
@router.get("/execution/{request_id}")
def execution_state(request_id:str,_:str=Depends(auth)):
    from .execution_pipeline import state_snapshot
    item=state_snapshot(request_id)
    if not item: raise HTTPException(404,"Execution not found")
    return item

@router.get("/tools/catalog")
def tool_catalog(_:str=Depends(auth)):
    data=legacy.rows("select id,name,description,kind,enabled from tools order by id")
    return {"count":len(data),"tools":data}

@router.post("/tools/run")
async def tool_run(payload:dict,_:str=Depends(auth)):
    from .execution_pipeline import run_pipeline
    name=str(payload.get("name","")).strip()
    if not name: raise HTTPException(400,"name required")
    return await run_pipeline(message="",kind="tool",target=name,args=payload.get("args") or {},request_id=payload.get("request_id"))

@router.post("/vorqyon/execute")
async def vorqyon_execute(payload:dict,_:str=Depends(auth)):
    from .execution_pipeline import run_pipeline
    mode=str(payload.get("mode","")).strip()
    target=str(payload.get("target","")).strip()
    if not mode or not target: raise HTTPException(400,"mode and target required")
    result=await run_pipeline(message=str(payload.get("message",target)),kind=mode,target=target,args=payload.get("args") or {},
                              require_approval=bool(payload.get("require_approval")),request_id=payload.get("request_id"))
    if result.get("state")=="Complete":
        expected=payload.get("expected")
        value=result.get("result") if mode!="chat" else result.get("answer")
        result["verification"]=_verify_result(value,expected)
        if not result["verification"]["ok"]: raise HTTPException(500,"Execution verification failed")
    return result

@router.post("/workflows/validate")
def validate_workflow(payload:dict,_:str=Depends(auth)):
    steps=payload.get("steps")
    if not isinstance(steps,list) or not steps: return {"valid":False,"errors":["steps must be a non-empty list"]}
    errors=[]
    for i,step in enumerate(steps):
        if not isinstance(step,dict): errors.append(f"step {i}: expected object"); continue
        kind=step.get("type",step.get("action",""))
        if kind not in {"tool","chat","memory","value","set","transform"}: errors.append(f"step {i}: unsupported action '{kind}'")
        if kind=="tool" and not legacy.one("select id from tools where id=? and enabled=1",(str(step.get("name","")),)):
            errors.append(f"step {i}: tool not found or disabled")
    return {"valid":not errors,"errors":errors}
