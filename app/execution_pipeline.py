"""Unified RAYONE execution pipeline.

Single control path for assistant, tools, workflows, media and research.
Native/local execution is preferred; configured providers are optional adapters.
"""
from __future__ import annotations
import asyncio, json, re, time, uuid, hashlib
from typing import Any

STATES=("Queued","Understanding","Planning","Authorized","Executing","Verifying","Complete","Failed","Awaiting Approval")

def _legacy():
    from . import main as legacy
    return legacy

def _advanced():
    try:
        from . import advanced
        return advanced
    except Exception:
        return None

def ensure_store():
    legacy=_legacy()
    legacy.execute("""CREATE TABLE IF NOT EXISTS execution_runs(
        request_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        intent TEXT,
        target TEXT,
        input TEXT NOT NULL,
        result TEXT,
        error TEXT,
        attempt INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL,
        updated REAL NOT NULL
    )""")

def _row(request_id):
    return _legacy().one("select * from execution_runs where request_id=?",(request_id,))

def _save(request_id,state,**fields):
    legacy=_legacy(); ensure_store()
    fields={k:v for k,v in fields.items() if k in {"intent","target","result","error","attempt"}}
    sets=["state=?","updated=?"]; args=[state,legacy.now()]
    for k,v in fields.items():
        sets.append(f"{k}=?"); args.append(legacy.dumps(v) if k=="result" else v)
    args.append(request_id)
    legacy.execute("update execution_runs set "+",".join(sets)+" where request_id=?",args)
    try: legacy.emit("execution.state",{"request_id":request_id,"state":state,"intent":fields.get("intent")})
    except Exception: pass

def _trace(request_id,state,payload=None):
    adv=_advanced()
    if adv:
        try: return adv.trace(state,request_id,payload or {})
        except Exception: pass
    try: _legacy().emit("rayone.state",{"request_id":request_id,"state":state,"payload":payload or {}})
    except Exception: pass
    return None

def _tokens(text):
    return set(re.findall(r"[A-Za-z0-9\u0900-\u097F]{3,}",str(text or "").lower()))

def classify(message:str)->str:
    m=message.lower().strip()
    if re.search(r"\b(search|research|find|latest|news|web)\b",m): return "research"
    if re.search(r"\b(calculate|compute|math|sum|add|subtract|multiply|divide)\b",m): return "tool"
    if re.search(r"\b(schedule|remind|every day|every hour|cron)\b",m): return "automation"
    if re.search(r"\b(image|video|audio|music|voice|tts|speech)\b",m): return "media"
    if re.search(r"\b(file|document|pdf|docx|xlsx|upload)\b",m): return "knowledge"
    if re.search(r"\b(github|gitlab|repository|commit|pull request|issue)\b",m): return "devops"
    return "chat"

def _verified(result):
    if result is None: return False
    if isinstance(result,dict):
        if result.get("status") in {"failed","error"}: return False
        if result.get("native") and result.get("size") is not None and result.get("size")<=0: return False
        if result.get("verified") is False: return False
    return True

async def _native_research(message):
    from .native_engines import research
    return research(message,max_sources=5)

def _native_media(kind,prompt):
    legacy=_legacy()
    result=legacy.BUILTIN_PACK[kind]["generate"]({"prompt":prompt})
    if not _verified(result): raise RuntimeError("Native media verification failed")
    return result

async def _chat(message,model_id=None):
    legacy=_legacy()
    adv=_advanced()
    try:
        if adv and hasattr(adv,"_provider_failover"):
            answer,provider,failures=await adv._provider_failover(message,model_id)
        else:
            answer,provider=await legacy.provider_chat(message,model_id)
            failures=0
        if answer:
            return {"answer":answer,"provider":provider or "provider","native":False,"provider_failures":failures}
    except Exception:
        try: legacy.metric("provider.failures")
        except Exception: pass
    mem=legacy.rows("select content from memories where content like ? order by created desc limit 5",(f"%{message[:40]}%",))
    answer="RAYONE local core received: "+message
    if mem: answer+="\nRelevant memory: "+" ".join(m["content"] for m in mem)
    return {"answer":answer,"provider":"local","native":True,"provider_failures":0}

async def run_pipeline(*,message:str,model_id=None,conversation_id=None,require_approval=False,
                       target=None,args=None,kind=None,request_id=None,subject="admin"):
    legacy=_legacy(); ensure_store()
    message=str(message or "").strip()
    request_id=request_id or str(uuid.uuid4())
    existing=_row(request_id)
    if existing and existing["state"]=="Complete":
        return json.loads(existing["result"] or "{}")
    if existing and existing["state"]=="Awaiting Approval":
        return json.loads(existing["result"] or "{}")
    if not existing:
        try:
            legacy.execute("insert into execution_runs values(?,?,?,?,?,?,?,?,?,?)",
                (request_id,"Queued",None,target or kind or "",legacy.dumps({"message":message,"args":args or {}}),
                 None,None,0,legacy.now(),legacy.now()))
        except Exception:
            existing=_row(request_id)
    _trace(request_id,"Queued")
    _save(request_id,"Understanding",intent=kind or (classify(message) if message else "tool"),target=target or "")
    intent=kind or (classify(message) if message else "tool")
    _trace(request_id,"Understanding",{"message":message,"intent":intent})
    _trace(request_id,"Planning",{"intent":intent,"target":target})
    if require_approval:
        adv=_advanced()
        if not adv: raise RuntimeError("Approval engine unavailable")
        aid=str(uuid.uuid4())
        payload={"message":message,"model_id":model_id,"conversation_id":conversation_id,
                 "target":target,"args":args or {},"kind":intent,"request_id":request_id}
        legacy.execute("insert into approvals values(?,?,?,?,?,?,?)",
                       (aid,"pipeline.execute",target or intent,legacy.dumps(payload),"pending",legacy.now(),None))
        result={"request_id":request_id,"state":"Awaiting Approval","approval_id":aid,"intent":intent}
        _save(request_id,"Awaiting Approval",result=result)
        _trace(request_id,"Awaiting Approval",{"approval_id":aid})
        return result
    _trace(request_id,"Authorized",{"subject":subject})
    _save(request_id,"Authorized")
    try:
        _trace(request_id,"Executing",{"intent":intent})
        if intent=="tool":
            name=target
            if not name:
                expr=re.sub(r"^(please\s+)?(calculate|compute)\s+","",message,flags=re.I)
                name="core.calculator"; args={"expression":expr}
            adv=_advanced()
            if adv and not adv.permission_allows(subject,"tool.execute",name):
                raise PermissionError("Tool execution denied by permission policy")
            result=await legacy.execute_tool_internal(name,args or {})
            if not _verified(result): raise RuntimeError("Tool verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"tool","target":name,"result":result}
        elif intent=="research":
            result=await _native_research(message)
            if not _verified(result): raise RuntimeError("Research verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"research","result":result}
        elif intent=="knowledge":
            from .native_engines import _document_extract
            path=str((args or {}).get("path","")).strip()
            if not path: raise ValueError("Document path required")
            result=_document_extract(path)
            if not _verified(result): raise RuntimeError("Document verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"knowledge","result":result}
        elif intent=="devops":
            from .native_engines import native_search
            result=native_search(message,10)
            if not _verified(result): raise RuntimeError("Workspace search verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"devops","result":result}
        elif intent=="media":
            low=message.lower()
            kind2=target or ("video" if "video" in low else "music" if "music" in low else
                             "audio" if "audio" in low else "voice" if any(x in low for x in ("voice","tts","speech")) else "image")
            result=_native_media(kind2,message)
            mid=str(uuid.uuid4()); t=legacy.now()
            legacy.execute("insert into media_jobs values(?,?,?,?,?,?,?,?)",
                (mid,kind2,"completed",legacy.dumps({"prompt":message}),legacy.dumps(result),"native",t,t))
            out={"request_id":request_id,"state":"Complete","intent":"media","media_job_id":mid,
                 "status":"completed","provider":"native","result":result}
        elif intent=="workflow":
            adv=_advanced()
            if adv and not adv.permission_allows(subject,"workflow.execute",str(target)):
                raise PermissionError("Workflow execution denied by permission policy")
            result=await legacy.run_workflow_internal(str(target),args or {})
            if not _verified(result): raise RuntimeError("Workflow verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"workflow","target":target,"result":result}
        elif intent=="automation":
            adv=_advanced()
            if not adv: raise RuntimeError("Automation engine unavailable")
            seconds=3600.0
            low=message.lower()
            m=re.search(r"(\d+)\s*(second|minute|hour|day)",low)
            if m:
                n=float(m.group(1)); unit=m.group(2)
                seconds=n*(1 if unit.startswith("second") else 60 if unit.startswith("minute") else 3600 if unit.startswith("hour") else 86400)
            elif "every hour" in low: seconds=3600.0
            elif "every day" in low: seconds=86400.0
            text=re.sub(r"^(please\s+)?(remind me|remind|schedule)\s*(every\s+\d+\s*(second|minute|hour|day)s?|every (hour|day))?\s*(to)?\s*","",message,flags=re.I).strip() or message
            sid=str(uuid.uuid4()); t=legacy.now()
            legacy.execute("insert into schedules values(?,?,?,?,?,?,?,?,?,?,?)",
                           (sid,"RAYONE reminder","interval",str(seconds),"chat",legacy.dumps({"message":text}),
                            1,t+seconds,None,t,t))
            result={"schedule_id":sid,"interval_seconds":seconds,"message":text,"verified":True}
            out={"request_id":request_id,"state":"Complete","intent":"automation","result":result}
        elif intent in {"knowledge","devops"}:
            from .native_engines import native_search
            result=native_search(message,10)
            if not _verified(result): raise RuntimeError("Workspace search verification failed")
            out={"request_id":request_id,"state":"Complete","intent":intent,"result":result}
        elif intent=="chat":
            result=await _chat(message,model_id)
            out={"request_id":request_id,"state":"Complete","intent":"chat",**result}
        else:
            result=await _chat(message,model_id)
            out={"request_id":request_id,"state":"Complete","intent":intent,**result}
        _trace(request_id,"Verifying",{"verified":True})
        _save(request_id,"Verifying",result=out)
        _save(request_id,"Complete",result=out)
        try: legacy.metric("execution.completed")
        except Exception: pass
        return out
    except Exception as e:
        _trace(request_id,"Failed",{"error":str(e)})
        _save(request_id,"Failed",error=str(e),attempt=(int((_row(request_id) or {}).get("attempt") or 0)+1))
        try: legacy.metric("execution.failed")
        except Exception: pass
        raise

async def execute_approved(payload):
    payload=dict(payload or {})
    payload["require_approval"]=False
    request_id=payload.get("request_id")
    if request_id:
        ensure_store()
        _save(request_id,"Queued")
    return await run_pipeline(message=str(payload.get("message","")),model_id=payload.get("model_id"),
        conversation_id=payload.get("conversation_id"),target=payload.get("target"),args=payload.get("args") or {},
        kind=payload.get("kind"),request_id=request_id)

def state_snapshot(request_id):
    ensure_store(); r=_row(request_id)
    if not r: return None
    out=dict(r)
    for k in ("input","result"):
        if out.get(k):
            try: out[k]=json.loads(out[k])
            except Exception: pass
    return out
