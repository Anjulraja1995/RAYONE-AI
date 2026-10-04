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

def _conversation_state(conversation_id):
    if not conversation_id: return {}
    row=_legacy().one("select * from conversation_state where conversation_id=?",(conversation_id,))
    if not row: return {}
    return {"conversation_id":row["conversation_id"],"pending_intent":row["pending_intent"],
            "pending_kind":row["pending_kind"],"pending_message":row["pending_message"],
            "last_request_id":row["last_request_id"],"last_state":row["last_state"],
            "last_result":json.loads(row["last_result"]) if row["last_result"] else None}

def _save_conversation_state(conversation_id, **fields):
    if not conversation_id: return
    legacy=_legacy(); cur=_conversation_state(conversation_id); cur.update(fields)
    legacy.execute("""insert into conversation_state
        (conversation_id,pending_intent,pending_kind,pending_message,last_request_id,last_state,last_result,updated)
        values(?,?,?,?,?,?,?,?)
        on conflict(conversation_id) do update set
        pending_intent=excluded.pending_intent,pending_kind=excluded.pending_kind,
        pending_message=excluded.pending_message,last_request_id=excluded.last_request_id,
        last_state=excluded.last_state,last_result=excluded.last_result,updated=excluded.updated""",
        (conversation_id,cur.get("pending_intent"),cur.get("pending_kind"),cur.get("pending_message"),
         cur.get("last_request_id"),cur.get("last_state"),
         legacy.dumps(cur.get("last_result")) if cur.get("last_result") is not None else None,legacy.now()))


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
    from .local_brain import classify as brain_classify
    return brain_classify(message)

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

async def _chat(message,model_id=None,intent='chat'):
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
    from .local_brain import local_response
    answer=local_response(message,intent)
    if mem:
        answer+="\n\nRelevant memory: "+" ".join(m["content"][:1000] for m in mem)
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
    context=[]
    if conversation_id:
        try:
            rows=legacy.rows("select content from messages where conversation_id=? order by created desc limit 8",(conversation_id,))
            context=list(reversed(rows or []))
        except Exception:
            context=[]
    from .local_brain import build_plan, calculator_expression, resolve_followup, is_capability_question
    state=_conversation_state(conversation_id)
    pending={"intent":state.get("pending_intent"),"kind":state.get("pending_kind"),"message":state.get("pending_message")} if state.get("pending_intent") else None
    if is_capability_question(message) and not kind:
        cap_intent=classify(message)
        low=message.lower()
        cap_kind=("video" if "video" in low else "music" if "music" in low else
                  "audio" if "audio" in low else "voice" if any(x in low for x in ("voice","tts","speech")) else
                  "image" if cap_intent=="media" else cap_intent)
        from .local_brain import local_response
        answer=local_response(message,cap_intent)
        out={"request_id":request_id,"state":"Complete","intent":"chat","pending":True,
             "pending_intent":cap_intent,"pending_kind":cap_kind,"answer":answer,"provider":"local"}
        _save_conversation_state(conversation_id,pending_intent=cap_intent,pending_kind=cap_kind,
                                 pending_message=message,last_request_id=request_id,last_state="Complete",last_result=out)
        _save(request_id,"Complete",intent="chat",target=cap_kind,result=out)
        _trace(request_id,"Complete",{"pending":True,"intent":cap_intent})
        return out
    resolved=resolve_followup(message,context,pending=pending)
    intent=kind or resolved.get("intent") or (classify(message) if message else "tool")
    if intent=="status":
        rid=state.get("last_request_id")
        last=_row(rid) if rid else None
        answer=("Previous request is "+str(last["state"])+". "+
                ("The result is ready." if last and last["state"]=="Complete" else
                 ("It is waiting for approval." if last and last["state"]=="Awaiting Approval" else
                  (str(last["error"] or "No additional error details.") if last else "There is no previous execution in this conversation."))))
        out={"request_id":request_id,"state":"Complete","intent":"status","answer":answer,"last_request_id":rid,
             "last_execution":json.loads(last["result"] or "{}") if last and last["result"] else None}
        _save(request_id,"Complete",intent="status",result=out)
        _trace(request_id,"Complete",{"status_for":rid})
        return out
    effective_message=resolved.get("message") or message
    if resolved.get("execute_pending")=="true":
        _save_conversation_state(conversation_id,pending_intent=None,pending_kind=None,pending_message=None,
                                 last_request_id=request_id,last_state="Executing",last_result=None)
    _save(request_id,"Understanding",intent=intent,target=target or "")
    _trace(request_id,"Understanding",{"message":message,"effective_message":effective_message,"intent":intent})
    plan=build_plan(effective_message,intent)
    _trace(request_id,"Planning",plan)
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
                expr=calculator_expression(message)
                name="core.calculator"; args={"expression":expr}
            adv=_advanced()
            if adv and not adv.permission_allows(subject,"tool.execute",name):
                raise PermissionError("Tool execution denied by permission policy")
            result=await legacy.execute_tool_internal(name,args or {})
            if not _verified(result): raise RuntimeError("Tool verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"tool","target":name,"plan":plan,"result":result}
        elif intent=="research":
            result=await _native_research(effective_message)
            if not _verified(result): raise RuntimeError("Research verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"research","plan":plan,"result":result}
        elif intent=="knowledge":
            from .native_engines import _document_extract
            path=str((args or {}).get("path","")).strip()
            if not path: raise ValueError("Document path required")
            result=_document_extract(path)
            if not _verified(result): raise RuntimeError("Document verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"knowledge","plan":plan,"result":result}
        elif intent=="devops":
            from .native_engines import native_search
            result=native_search(message,10)
            if not _verified(result): raise RuntimeError("Workspace search verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"devops","plan":plan,"result":result}
        elif intent=="media":
            low=message.lower()
            kind2=target or ("video" if "video" in low else "music" if "music" in low else
                             "audio" if "audio" in low else "voice" if any(x in low for x in ("voice","tts","speech")) else "image")
            result=_native_media(kind2,effective_message)
            mid=str(uuid.uuid4()); t=legacy.now()
            legacy.execute("insert into media_jobs values(?,?,?,?,?,?,?,?)",
                (mid,kind2,"completed",legacy.dumps({"prompt":effective_message}),legacy.dumps(result),"native",t,t))
            out={"request_id":request_id,"state":"Complete","intent":"media","plan":plan,"media_job_id":mid,
                 "status":"completed","provider":"native","result":result}
        elif intent=="workflow":
            adv=_advanced()
            if adv and not adv.permission_allows(subject,"workflow.execute",str(target)):
                raise PermissionError("Workflow execution denied by permission policy")
            result=await legacy.run_workflow_internal(str(target),args or {})
            if not _verified(result): raise RuntimeError("Workflow verification failed")
            out={"request_id":request_id,"state":"Complete","intent":"workflow","target":target,"plan":plan,"result":result}
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
            out={"request_id":request_id,"state":"Complete","intent":"automation","plan":plan,"result":result}
        elif intent in {"knowledge","devops"}:
            from .native_engines import native_search
            result=native_search(message,10)
            if not _verified(result): raise RuntimeError("Workspace search verification failed")
            out={"request_id":request_id,"state":"Complete","intent":intent,"result":result}
        elif intent=="chat":
            result=await _chat(effective_message,model_id,intent)
            out={"request_id":request_id,"state":"Complete","intent":"chat","plan":plan,**result}
        else:
            result=await _chat(message,model_id,intent)
            out={"request_id":request_id,"state":"Complete","intent":intent,"plan":plan,**result}
        _trace(request_id,"Verifying",{"verified":True})
        _save(request_id,"Verifying",result=out)
        _save(request_id,"Complete",result=out)
        _save_conversation_state(conversation_id,pending_intent=None,pending_kind=None,pending_message=None,
                                 last_request_id=request_id,last_state="Complete",last_result=out)
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
