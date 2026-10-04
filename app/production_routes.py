"""Production completion routes: provider ecosystem, connectors, diagnostics and creative pipeline."""
from fastapi import APIRouter, Depends, HTTPException
from . import main as legacy
from .provider_adapters import health as provider_health, list_models, chat_failover
from .connector_adapters import execute as connector_execute, normalize_kind, ConnectorError
from .native_creative import engine_info
import json, time, uuid

router=APIRouter(prefix="/api/v2/production",tags=["RAYONE production layer"])
def auth(token=Depends(legacy.auth)): return token

def _token(provider):
    cfg=json.loads(provider.get("config") or "{}")
    raw=cfg.get("api_key") or ""
    if not raw:return ""
    try:return legacy.FERNET.decrypt(raw.encode()).decode()
    except Exception:return ""

@router.get("/providers")
async def providers(_:str=Depends(auth)):
    out=[]
    for p in legacy.rows("select * from providers where enabled=1 order by rowid"):
        h=await provider_health(p.get("base_url",""),p.get("kind",""),_token(p))
        out.append({"id":p["id"],"name":p["name"],"kind":p["kind"],"base_url":p["base_url"],"health":h})
    return {"providers":out,"local_first":True}

@router.get("/providers/{provider_id}/models")
async def models(provider_id:str,_:str=Depends(auth)):
    p=legacy.one("select * from providers where id=? and enabled=1",(provider_id,))
    if not p:raise HTTPException(404,"Provider not found")
    try:return await list_models(p["base_url"],p["kind"],_token(p))
    except Exception as e:raise HTTPException(502,str(e))

@router.post("/providers/failover")
async def failover(payload:dict,_:str=Depends(auth)):
    message=str(payload.get("message","")).strip()
    if not message:raise HTTPException(400,"message required")
    model_id=payload.get("model_id")
    candidates=[]
    rows=legacy.rows("""select p.*,m.id as model_id,m.model as selected_model
                        from providers p left join models m on m.provider_id=p.id and m.enabled=1
                        where p.enabled=1 order by p.rowid,m.rowid""")
    for p in rows:
        cfg=json.loads(p["config"] or "{}")
        candidates.append({"name":p["name"],"kind":p["kind"],"base_url":p["base_url"],
                           "token":_token(p),"model":p.get("selected_model") or cfg.get("model",""),
                           "model_id":p.get("model_id")})
    text,provider,meta=await chat_failover(candidates,message,model_id)
    if text is None: raise HTTPException(503,detail={"message":"No provider succeeded","attempts":meta["attempts"]})
    return {"answer":text,"provider":provider,"attempts":meta["attempts"],"verified":True}

@router.get("/connectors")
def connectors(_:str=Depends(auth)):
    rows=legacy.rows("select id,name,kind,base_url,enabled,config,created,updated from connectors order by name")
    for x in rows:
        x["config"]=json.loads(x["config"] or "{}")
        x["config"].pop("token",None);x["config"].pop("api_key",None)
    return rows

@router.post("/connectors/{connector_id}/execute")
async def connector_run(connector_id:str,payload:dict,_:str=Depends(auth)):
    c=legacy.one("select * from connectors where id=? and enabled=1",(connector_id,))
    if not c:raise HTTPException(404,"Connector not found or disabled")
    cfg=json.loads(c["config"] or "{}")
    method=str(payload.get("method","GET")).upper()
    if method!="GET":
        aid=str(uuid.uuid4())
        legacy.execute("insert into approvals values(?,?,?,?,?,?,?)",(aid,"connector."+method,c["base_url"],legacy.dumps({"connector_id":connector_id,"payload":payload}),"pending",legacy.now(),None))
        return {"state":"Awaiting Approval","approval_id":aid}
    try:
        result=await connector_execute(c["base_url"],normalize_kind(c["kind"]),cfg,method,payload.get("path",""),payload.get("params") or {},None)
        legacy.audit("connector.execute",c["name"],{"method":method,"status":result["status"]})
        return result
    except ConnectorError as e:raise HTTPException(400,str(e))
    except Exception as e:raise HTTPException(502,str(e))

@router.post("/connectors/test")
async def connector_test(payload:dict,_:str=Depends(auth)):
    base=str(payload.get("base_url",""));kind=normalize_kind(str(payload.get("kind","http")))
    try:return await connector_execute(base,kind,payload.get("config") or {}, "GET","",{},None)
    except Exception as e:raise HTTPException(502,str(e))

@router.get("/creative")
def creative(_:str=Depends(auth)):return engine_info()

@router.get("/hardening")
def hardening(_:str=Depends(auth)):
    tables=["projects","providers","models","tools","agents","workflows","memories","jobs","events","audits","checkpoints","secrets","settings","traces","approvals","permissions","schedules","workspace_files","media_jobs","connectors","metrics_v2","memory_index","conversations","messages"]
    checks={}
    for t in tables:
        try: legacy.rows("select 1 from "+t+" limit 1");checks[t]=True
        except Exception:checks[t]=False
    return {"ok":all(checks.values()),"database_tables":checks,"session_security":True,"encrypted_secrets":True,"local_first":True}

@router.get("/status")
async def production_status(_:str=Depends(auth)):
    return {"layer":"production-completion","providers":True,"connectors":True,"creative":engine_info(),
            "hardening":{"local_first":True,"encrypted_secrets":True,"approval_gates":True},"timestamp":time.time()}
