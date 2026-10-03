from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
import sqlite3, json, uuid, time, hashlib, os, base64, secrets, asyncio, ast, operator, math, re
import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.getenv("RAYONE_DB", ROOT / "data" / "rayone.db"))
DB.parent.mkdir(parents=True, exist_ok=True)
APP_VERSION = "2.0.0"
ADMIN_PASSWORD = os.getenv("RAYONE_ADMIN_PASSWORD", "RAYONE-Admin-2026")
SECRET_KEY = os.getenv("RAYONE_SECRET_KEY", "")
if not SECRET_KEY:
    seed = os.getenv("RAYONE_SECRET_SEED", "rayone-local-first-v2")
    SECRET_KEY = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest()).decode()
FERNET = Fernet(SECRET_KEY.encode())
app = FastAPI(title="RAYONE AI", version=APP_VERSION)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,status TEXT NOT NULL,config TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,base_url TEXT,enabled INTEGER NOT NULL,config TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,name TEXT NOT NULL,provider_id TEXT,model TEXT NOT NULL,enabled INTEGER NOT NULL,config TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tools(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,kind TEXT NOT NULL,enabled INTEGER NOT NULL,config TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,model_id TEXT,enabled INTEGER NOT NULL,config TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,name TEXT NOT NULL,steps TEXT NOT NULL,enabled INTEGER NOT NULL,version INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,scope TEXT NOT NULL,content TEXT NOT NULL,metadata TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,type TEXT NOT NULL,status TEXT NOT NULL,input TEXT NOT NULL,result TEXT,error TEXT,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,type TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audits(id TEXT PRIMARY KEY,action TEXT NOT NULL,target TEXT NOT NULL,detail TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,target TEXT NOT NULL,snapshot TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS secrets(id TEXT PRIMARY KEY,name TEXT NOT NULL,ciphertext TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,created REAL NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated REAL NOT NULL);
'''

def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def now(): return time.time()
def dumps(x): return json.dumps(x, ensure_ascii=False, separators=(",", ":"))
def rows(sql, args=()):
    c = conn(); r = [dict(x) for x in c.execute(sql, args).fetchall()]; c.close(); return r

def one(sql, args=()):
    c = conn(); x = c.execute(sql, args).fetchone(); c.close(); return dict(x) if x else None

def execute(sql, args=()):
    c = conn(); cur = c.execute(sql, args); c.commit(); v = cur.lastrowid; c.close(); return v

def emit(t, p): execute("INSERT INTO events VALUES(?,?,?,?)", (str(uuid.uuid4()), t, dumps(p), now()))
def audit(a, t, d): execute("INSERT INTO audits VALUES(?,?,?,?,?)", (str(uuid.uuid4()), a, t, dumps(d), now()))

def init():
    c = conn(); c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
        t = now(); c.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), "RAYONE Core", "system", "online", "{}", t, t))
    builtin = [
        ("core.echo", "core.echo", "Return text unchanged", "builtin"),
        ("core.calculator", "core.calculator", "Safely evaluate arithmetic expressions", "builtin"),
        ("core.datetime", "core.datetime", "Return current server time", "builtin"),
        ("core.memory_search", "core.memory_search", "Search stored memories", "builtin"),
        ("core.json", "core.json", "Parse and normalize JSON", "builtin"),
    ]
    for tid, name, desc, kind in builtin:
        if not c.execute("SELECT 1 FROM tools WHERE id=?", (tid,)).fetchone():
            t=now(); c.execute("INSERT INTO tools VALUES(?,?,?,?,?,?,?,?)", (tid,name,desc,kind,1,"{}",t,t))
    c.commit(); c.close()
init()

class LoginIn(BaseModel): password: str
class Chat(BaseModel): message: str = Field(min_length=1); project_id: str|None = None; agent_id: str|None = None; model_id: str|None = None
class ProviderIn(BaseModel): name: str; kind: str = "openai_compatible"; base_url: str = ""; api_key: str = ""; enabled: bool = True; config: dict = {}
class ModelIn(BaseModel): name: str; provider_id: str|None = None; model: str; enabled: bool = True; config: dict = {}
class MemoryIn(BaseModel): scope: str = "global"; content: str; metadata: dict = {}
class JobIn(BaseModel): type: str = "chat"; input: dict = {}
class ToolCall(BaseModel): name: str; args: dict = {}
class ToolIn(BaseModel): name: str; description: str; kind: str = "http"; enabled: bool = True; config: dict = {}
class WorkflowIn(BaseModel): name: str; steps: list[dict] = []; enabled: bool = True
class AgentIn(BaseModel): name: str; description: str = ""; model_id: str|None = None; enabled: bool = True; config: dict = {}
class ProjectIn(BaseModel): name: str; type: str = "app"; config: dict = {}
class SecretIn(BaseModel): name: str; value: str
class SettingIn(BaseModel): value: str

@app.post("/api/auth/login")
def login(x: LoginIn):
    if not secrets.compare_digest(x.password, ADMIN_PASSWORD):
        audit("login_failed", "auth", {})
        raise HTTPException(401, "Invalid credentials")
    token = secrets.token_urlsafe(32); execute("INSERT INTO sessions VALUES(?,?,?)", (token, now(), now()+86400))
    audit("login", "auth", {})
    return {"token": token, "expires_in": 86400}

@app.post("/api/auth/logout")
def logout(authorization: str|None = Header(default=None)):
    token = authorization.replace("Bearer ", "", 1) if authorization else ""
    if token: execute("DELETE FROM sessions WHERE token=?", (token,))
    return {"ok": True}

def auth(authorization: str|None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization[7:]
    s = one("SELECT * FROM sessions WHERE token=? AND expires>?", (token, now()))
    if not s: raise HTTPException(401, "Session expired or invalid")
    return token

@app.get("/api/health")
def health():
    return {"ok": True, "name": "RAYONE AI", "version": APP_VERSION, "database": "sqlite", "mode": "free-local-first", "authenticated_admin": True}

@app.get("/api/summary")
def summary(_: str = Depends(auth)):
    return {t: rows(f"select count(*) n from {t}")[0]["n"] for t in ["projects","providers","models","tools","agents","workflows","memories","jobs","events","audits","checkpoints","secrets"]}

# Generic CRUD helpers
@app.get("/api/projects")
def projects(_: str = Depends(auth)): return rows("select * from projects order by updated desc")
@app.post("/api/projects")
def add_project(x: ProjectIn, _: str = Depends(auth)):
    i=str(uuid.uuid4()); t=now(); execute("insert into projects values(?,?,?,?,?,?,?)",(i,x.name,x.type,"active",dumps(x.config),t,t)); audit("create","project",{"id":i,"name":x.name}); return {"id":i}
@app.put("/api/projects/{id}")
def update_project(id: str, x: ProjectIn, _: str = Depends(auth)):
    if not one("select id from projects where id=?",(id,)): raise HTTPException(404,"Project not found")
    execute("update projects set name=?,type=?,config=?,updated=? where id=?",(x.name,x.type,dumps(x.config),now(),id)); audit("update","project",{"id":id}); return {"ok":True}
@app.delete("/api/projects/{id}")
def delete_project(id: str, _: str = Depends(auth)):
    execute("delete from projects where id=?",(id,)); audit("delete","project",{"id":id}); return {"ok":True}

@app.get("/api/providers")
def providers(_: str = Depends(auth)):
    data=rows("select id,name,kind,base_url,enabled,config,created,updated from providers order by name")
    for p in data: p["config"] = json.loads(p["config"] or "{}")
    return data
@app.post("/api/providers")
def add_provider(x: ProviderIn, _: str = Depends(auth)):
    i=str(uuid.uuid4()); t=now(); cfg=dict(x.config)
    if x.api_key: cfg["api_key"] = FERNET.encrypt(x.api_key.encode()).decode()
    execute("insert into providers values(?,?,?,?,?,?,?,?)",(i,x.name,x.kind,x.base_url.rstrip("/"),int(x.enabled),dumps(cfg),t,t)); audit("create","provider",{"id":i,"name":x.name}); return {"id":i}
@app.put("/api/providers/{id}")
def update_provider(id: str, x: ProviderIn, _: str = Depends(auth)):
    old=one("select * from providers where id=?",(id,));
    if not old: raise HTTPException(404,"Provider not found")
    cfg=dict(x.config); key=x.api_key
    if key: cfg["api_key"]=FERNET.encrypt(key.encode()).decode()
    else: cfg["api_key"]=json.loads(old["config"] or "{}").get("api_key","")
    execute("update providers set name=?,kind=?,base_url=?,enabled=?,config=?,updated=? where id=?",(x.name,x.kind,x.base_url.rstrip("/"),int(x.enabled),dumps(cfg),now(),id)); audit("update","provider",{"id":id}); return {"ok":True}
@app.delete("/api/providers/{id}")
def delete_provider(id: str, _: str = Depends(auth)): execute("delete from providers where id=?",(id,)); audit("delete","provider",{"id":id}); return {"ok":True}

@app.get("/api/models")
def models(_: str = Depends(auth)): return rows("select * from models order by name")
@app.post("/api/models")
def add_model(x: ModelIn, _: str = Depends(auth)):
    i=str(uuid.uuid4()); t=now(); execute("insert into models values(?,?,?,?,?,?,?,?)",(i,x.name,x.provider_id,x.model,int(x.enabled),dumps(x.config),t,t)); audit("create","model",{"id":i}); return {"id":i}
@app.put("/api/models/{id}")
def update_model(id: str,x:ModelIn,_:str=Depends(auth)):
    execute("update models set name=?,provider_id=?,model=?,enabled=?,config=?,updated=? where id=?",(x.name,x.provider_id,x.model,int(x.enabled),dumps(x.config),now(),id)); return {"ok":True}
@app.delete("/api/models/{id}")
def delete_model(id:str,_:str=Depends(auth)): execute("delete from models where id=?",(id,)); return {"ok":True}

@app.get("/api/tools")
def tools(_: str = Depends(auth)): return rows("select * from tools order by name")
@app.post("/api/tools")
def add_tool(x:ToolIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=now();execute("insert into tools values(?,?,?,?,?,?,?)",(i,x.name,x.description,x.kind,int(x.enabled),dumps(x.config),t,t));return {"id":i}
@app.put("/api/tools/{id}")
def update_tool(id:str,x:ToolIn,_:str=Depends(auth)): execute("update tools set name=?,description=?,kind=?,enabled=?,config=?,updated=? where id=?",(x.name,x.description,x.kind,int(x.enabled),dumps(x.config),now(),id));return {"ok":True}
@app.delete("/api/tools/{id}")
def delete_tool(id:str,_:str=Depends(auth)): execute("delete from tools where id=?",(id,));return {"ok":True}

@app.get("/api/agents")
def agents(_:str=Depends(auth)): return rows("select * from agents order by name")
@app.post("/api/agents")
def add_agent(x:AgentIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=now();execute("insert into agents values(?,?,?,?,?,?,?)",(i,x.name,x.description,x.model_id,int(x.enabled),dumps(x.config),t,t));return {"id":i}
@app.put("/api/agents/{id}")
def update_agent(id:str,x:AgentIn,_:str=Depends(auth)):execute("update agents set name=?,description=?,model_id=?,enabled=?,config=?,updated=? where id=?",(x.name,x.description,x.model_id,int(x.enabled),dumps(x.config),now(),id));return {"ok":True}
@app.delete("/api/agents/{id}")
def delete_agent(id:str,_:str=Depends(auth)):execute("delete from agents where id=?",(id,));return {"ok":True}

@app.get("/api/workflows")
def workflows(_:str=Depends(auth)):
    out=rows("select * from workflows order by updated desc")
    for x in out:x["steps"]=json.loads(x["steps"])
    return out
@app.post("/api/workflows")
def add_workflow(x:WorkflowIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=now();execute("insert into workflows values(?,?,?,?,?,?,?)",(i,x.name,dumps(x.steps),int(x.enabled),1,t,t));audit("create","workflow",{"id":i});return {"id":i}
@app.put("/api/workflows/{id}")
def update_workflow(id:str,x:WorkflowIn,_:str=Depends(auth)):
    old=one("select version from workflows where id=?",(id,));
    if not old:raise HTTPException(404,"Workflow not found")
    execute("update workflows set name=?,steps=?,enabled=?,version=?,updated=? where id=?",(x.name,dumps(x.steps),int(x.enabled),old["version"]+1,now(),id));return {"ok":True,"version":old["version"]+1}
@app.delete("/api/workflows/{id}")
def delete_workflow(id:str,_:str=Depends(auth)):execute("delete from workflows where id=?",(id,));return {"ok":True}

@app.post("/api/memory")
def memory(x:MemoryIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());execute("insert into memories values(?,?,?,?,?)",(i,x.scope,x.content,dumps(x.metadata),now()));emit("memory.write",{"id":i,"scope":x.scope});return {"id":i}
@app.get("/api/memory/search")
def memory_search(q:str="",scope:str|None=None,limit:int=50,_:str=Depends(auth)):
    sql="select * from memories where content like ?";args=[f"%{q}%"]
    if scope:sql+=" and scope=?";args.append(scope)
    return rows(sql+" order by created desc limit ?",args+[min(limit,200)])
@app.delete("/api/memory/{id}")
def delete_memory(id:str,_:str=Depends(auth)):execute("delete from memories where id=?",(id,));return {"ok":True}

# Safe calculator
def calc(expr):
    allowed={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Pow:operator.pow,ast.Mod:operator.mod,ast.USub:operator.neg,ast.UAdd:operator.pos}
    tree=ast.parse(expr,mode="eval")
    def ev(n):
        if isinstance(n,ast.Constant) and isinstance(n.value,(int,float)):return n.value
        if isinstance(n,ast.UnaryOp) and type(n.op) in allowed:return allowed[type(n.op)](ev(n.operand))
        if isinstance(n,ast.BinOp) and type(n.op) in allowed:return allowed[type(n.op)](ev(n.left),ev(n.right))
        raise ValueError("Only arithmetic is allowed")
    v=ev(tree.body)
    if not math.isfinite(float(v)):raise ValueError("Invalid result")
    return v

async def execute_tool_internal(name,args):
    tool=one("select * from tools where id=? and enabled=1",(name,))
    if not tool:raise HTTPException(404,"Tool is not registered or enabled")
    if name=="core.echo":result=args.get("text","")
    elif name=="core.calculator":result=calc(str(args.get("expression","")))
    elif name=="core.datetime":result=time.strftime("%Y-%m-%d %H:%M:%S %Z",time.localtime())
    elif name=="core.memory_search":result=rows("select * from memories where content like ? order by created desc limit 20",(f"%{args.get('query','')}%",))
    elif name=="core.json":result=json.loads(args.get("value","{}"))
    elif tool["kind"]=="http":
        cfg=json.loads(tool["config"] or "{}");url=cfg.get("url","")
        if not re.match(r"^https?://",url):raise ValueError("Tool URL must be http(s)")
        async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:r=await client.get(url,params=args.get("params",{}));result={"status":r.status_code,"text":r.text[:10000]}
    else: raise HTTPException(400,"Unsupported tool kind")
    emit("tool.completed",{"tool":name});audit("execute","tool",{"name":name});return result

@app.post("/api/tools/execute")
async def execute_tool(x:ToolCall,_:str=Depends(auth)):return {"ok":True,"tool":x.name,"result":await execute_tool_internal(x.name,x.args)}

async def provider_chat(message, model_id=None):
    if model_id:
        m=one("select * from models where id=? and enabled=1",(model_id,));p=one("select * from providers where id=? and enabled=1",(m["provider_id"],)) if m else None
    else:
        m=one("select * from models where enabled=1 order by rowid limit 1");p=one("select * from providers where id=? and enabled=1",(m["provider_id"],)) if m else one("select * from providers where enabled=1 and base_url<>'' order by rowid limit 1")
    if not p or not p["base_url"]:return None,None
    cfg=json.loads(p["config"] or "{}"); token=""
    if cfg.get("api_key"):
        try:token=FERNET.decrypt(cfg["api_key"].encode()).decode()
        except InvalidToken:token=""
    model=(m["model"] if m else cfg.get("model","default"))
    url=p["base_url"].rstrip("/")+"/chat/completions"
    headers={"Authorization":f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=45) as client:
        r=await client.post(url,headers=headers,json={"model":model,"messages":[{"role":"user","content":message}]});r.raise_for_status();d=r.json();return d["choices"][0]["message"]["content"],p["name"]

@app.post("/api/chat")
async def chat(x:Chat,_:str=Depends(auth)):
    # lightweight local planner: detect common tool intents before model routing
    msg=x.message.strip(); low=msg.lower()
    try:
        if low.startswith("calculate "):
            return {"mode":"tool","tool":"core.calculator","answer":str(await execute_tool_internal("core.calculator",{"expression":msg[10:]}))}
        if low in {"time","current time","what time is it"}:
            return {"mode":"tool","tool":"core.datetime","answer":str(await execute_tool_internal("core.datetime",{}))}
        answer,provider=await provider_chat(msg,x.model_id)
        if answer is not None:
            emit("chat.completed",{"provider":provider});return {"mode":"provider","provider":provider,"answer":answer}
    except Exception as e:emit("provider.error",{"error":str(e)})
    # local-first useful fallback with memory context
    mem=rows("select content from memories where content like ? order by created desc limit 5",(f"%{msg[:40]}%",))
    context=" ".join(m["content"] for m in mem)
    answer=f"RAYONE local core received: {msg}"
    if context:answer+=f"\nRelevant memory: {context}"
    emit("chat.completed",{"mode":"local"});return {"mode":"local","answer":answer}

@app.post("/api/jobs")
def create_job(x:JobIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=now();execute("insert into jobs values(?,?,?,?,?,?,?,?)",(i,x.type,"queued",dumps(x.input),None,None,t,t));emit("job.queued",{"id":i,"type":x.type});return {"id":i,"status":"queued"}
@app.get("/api/jobs")
def jobs(_:str=Depends(auth)):return rows("select * from jobs order by created desc limit 200")
@app.get("/api/jobs/{id}")
def job(id:str,_:str=Depends(auth)):
    x=one("select * from jobs where id=?",(id,));
    if not x:raise HTTPException(404,"Job not found")
    return x

async def worker_loop():
    while True:
        try:
            j=one("select * from jobs where status='queued' order by created limit 1")
            if j:
                execute("update jobs set status='running',updated=? where id=?",(now(),j["id"]))
                try:
                    inp=json.loads(j["input"] or "{}")
                    if j["type"]=="chat":res=await chat(Chat(message=inp.get("message", "")),"system")
                    elif j["type"]=="tool":res=await execute_tool_internal(inp["name"],inp.get("args",{}))
                    elif j["type"]=="workflow":res=await run_workflow_internal(inp["workflow_id"],inp.get("input",{}))
                    else:res={"error":"Unknown job type"}
                    execute("update jobs set status='completed',result=?,updated=? where id=?",(dumps(res),now(),j["id"]));emit("job.completed",{"id":j["id"]})
                except Exception as e:execute("update jobs set status='failed',error=?,updated=? where id=?",(str(e),now(),j["id"]));emit("job.failed",{"id":j["id"],"error":str(e)})
            else:await asyncio.sleep(.4)
        except Exception:await asyncio.sleep(1)

async def run_workflow_internal(wid, input_data):
    w=one("select * from workflows where id=? and enabled=1",(wid,));
    if not w:raise ValueError("Workflow not found or disabled")
    data=input_data.copy() if isinstance(input_data,dict) else {"input":input_data};outputs=[]
    for step in json.loads(w["steps"]):
        kind=step.get("type",step.get("action",""))
        if kind=="tool":out=await execute_tool_internal(step["name"],step.get("args",data))
        elif kind=="chat":out=(await provider_chat(str(step.get("message",data.get("message",data)))))[0] or f"RAYONE local core received: {data}"
        elif kind=="memory":out=execute("insert into memories values(?,?,?,?,?)",(str(uuid.uuid4()),step.get("scope","workflow"),str(step.get("content",data)),dumps(step.get("metadata",{})),now()))
        else:out=step.get("value",data)
        outputs.append(out);data={"previous":out,"outputs":outputs}
    emit("workflow.completed",{"id":wid});return {"workflow_id":wid,"outputs":outputs,"result":data}

@app.post("/api/workflows/{id}/run")
async def run_workflow(id:str,payload:dict|None=None,_:str=Depends(auth)):return await run_workflow_internal(id,payload or {})

@app.post("/api/checkpoints")
def checkpoint(target:str="system",_:str=Depends(auth)):
    tables=["projects","providers","models","tools","agents","workflows","memories","settings"]
    snapshot={t:rows(f"select * from {t}") for t in tables};i=str(uuid.uuid4());execute("insert into checkpoints values(?,?,?,?)",(i,target,dumps(snapshot),now()));audit("checkpoint",target,{"id":i});return {"id":i}
@app.get("/api/checkpoints")
def checkpoints(_:str=Depends(auth)):return rows("select id,target,created from checkpoints order by created desc")
@app.post("/api/checkpoints/{id}/restore")
def restore_checkpoint(id:str,_:str=Depends(auth)):
    cp=one("select * from checkpoints where id=?",(id,));
    if not cp:raise HTTPException(404,"Checkpoint not found")
    snap=json.loads(cp["snapshot"]);c=conn()
    tables=["projects","providers","models","tools","agents","workflows","memories","settings"]
    for t in tables:
        c.execute(f"delete from {t}")
        data=snap.get(t,[])
        if data:
            cols=list(data[0].keys());q=",".join("?" for _ in cols);c.executemany(f"insert into {t} ({','.join(cols)}) values ({q})",[[x[k] for k in cols] for x in data])
    c.commit();c.close();audit("restore","checkpoint",{"id":id});return {"ok":True}

@app.get("/api/export")
def export_state(_:str=Depends(auth)):
    return {t:rows(f"select * from {t}") for t in ["projects","providers","models","tools","agents","workflows","memories","jobs","events","audits","checkpoints","secrets","settings"]}
@app.post("/api/import")
def import_state(payload:dict,_:str=Depends(auth)):
    allowed=["projects","providers","models","tools","agents","workflows","memories","settings"]
    c=conn()
    for t in allowed:
        if t not in payload or not payload[t]:continue
        data=payload[t];cols=list(data[0].keys());q=",".join("?" for _ in cols)
        c.executemany(f"insert or replace into {t} ({','.join(cols)}) values ({q})",[[x.get(k) for k in cols] for x in data])
    c.commit();c.close();audit("import","state",{"tables":list(payload.keys())});return {"ok":True}

@app.get("/api/secrets")
def list_secrets(_:str=Depends(auth)):return [{"id":x["id"],"name":x["name"],"created":x["created"],"updated":x["updated"]} for x in rows("select * from secrets order by name")]
@app.post("/api/secrets")
def add_secret(x:SecretIn,_:str=Depends(auth)):
    i=str(uuid.uuid4());t=now();cipher=FERNET.encrypt(x.value.encode()).decode();execute("insert into secrets values(?,?,?,?,?)",(i,x.name,cipher,t,t));audit("create","secret",{"id":i,"name":x.name});return {"id":i}
@app.delete("/api/secrets/{id}")
def delete_secret(id:str,_:str=Depends(auth)):execute("delete from secrets where id=?",(id,));return {"ok":True}

@app.get("/api/settings")
def settings(_:str=Depends(auth)):return {x["key"]:x["value"] for x in rows("select * from settings")}
@app.put("/api/settings/{key}")
def setting(key:str,x:SettingIn,_:str=Depends(auth)):execute("insert or replace into settings values(?,?,?)",(key,x.value,now()));return {"ok":True}

@app.get("/api/audits")
def audits(limit:int=200,_:str=Depends(auth)):return rows("select * from audits order by created desc limit ?",(min(limit,500),))
@app.get("/api/events")
def events(limit:int=200,_:str=Depends(auth)):return rows("select * from events order by created desc limit ?",(min(limit,500),))
@app.get("/api/connectivity")
async def connectivity(_:str=Depends(auth)):
    results=[]
    for p in rows("select * from providers where enabled=1"):
        ok=False;err=""
        try:
            async with httpx.AsyncClient(timeout=8) as client:r=await client.get(p["base_url"]);ok=r.status_code<500
        except Exception as e:err=str(e)
        results.append({"id":p["id"],"name":p["name"],"ok":ok,"error":err})
    return {"database":True,"providers":results,"tools":len(rows("select id from tools where enabled=1")),"agents":len(rows("select id from agents where enabled=1")),"workflows":len(rows("select id from workflows where enabled=1"))}

HTML='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>RAYONE AI</title><style>
*{box-sizing:border-box}body{margin:0;background:#071016;color:#e8f1f5;font:14px system-ui,Segoe UI,sans-serif}header{height:64px;border-bottom:1px solid #24333a;display:flex;align-items:center;padding:0 22px;gap:18px;background:#0b151b;position:sticky;top:0;z-index:5}.logo{font-size:22px;font-weight:800;letter-spacing:2px}.pill{padding:7px 11px;border:1px solid #31505c;border-radius:999px;color:#8fe0b3}.spacer{flex:1}.layout{display:grid;grid-template-columns:225px 1fr;min-height:calc(100vh - 64px)}nav{border-right:1px solid #24333a;padding:16px 11px;background:#09131a}nav button{display:block;width:100%;text-align:left;background:transparent;color:#b9c8ce;border:0;border-radius:10px;padding:11px;margin:3px 0;cursor:pointer}nav button:hover,nav button.active{background:#13242c;color:white}.main{padding:22px;max-width:1500px;width:100%;margin:auto}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:#0d1a21;border:1px solid #23353e;border-radius:16px;padding:17px;box-shadow:0 8px 24px #0003}.num{font-size:27px;font-weight:800;margin-top:7px}.muted{color:#8da0a8}.chat{margin-top:18px}.messages{min-height:360px;max-height:55vh;overflow:auto}.msg{padding:12px 14px;border-radius:12px;background:#12232b;margin:9px 0;white-space:pre-wrap}.you{background:#19382d}.row{display:flex;gap:9px}input,textarea,select{background:#081218;color:#eef;border:1px solid #2a414c;border-radius:10px;padding:11px;width:100%}button.primary{background:#dceee5;color:#071016;border:0;border-radius:10px;padding:11px 16px;font-weight:700;cursor:pointer}button.secondary{background:#13242c;color:#dceee5;border:1px solid #31505c;border-radius:10px;padding:10px 14px;cursor:pointer}.section{display:none}.section.active{display:block}.list{display:grid;gap:9px;margin-top:14px}.item{padding:12px;border:1px solid #23353e;border-radius:10px;background:#0b171d}.toolbar{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0}.hidden{display:none}.danger{color:#ff9d9d}.login{max-width:420px;margin:12vh auto}.status{display:inline-block;width:9px;height:9px;border-radius:50%;background:#8fe0b3;margin-right:6px}.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}.small{font-size:12px}.code{font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap}@media(max-width:850px){.layout{grid-template-columns:1fr}nav{display:flex;overflow:auto;border-right:0;border-bottom:1px solid #24333a}.grid{grid-template-columns:repeat(2,1fr)}.two{grid-template-columns:1fr}}@media(max-width:520px){.grid{grid-template-columns:1fr}.main{padding:14px}}
</style></head><body><div id="root"></div><script>
const $=id=>document.getElementById(id);let token=localStorage.rayone_token||'';const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(u,o={}){o.headers={...(o.headers||{}),...(token?{Authorization:'Bearer '+token}:{})};let r=await fetch(u,o);if(r.status===401){logout(false);throw Error('Authentication required')}if(!r.ok)throw Error(await r.text());return r.json()}
function loginUI(){root.innerHTML=`<div class=login><div class=card><h1>RAYONE AI</h1><p class=muted>Universal AI Command Center</p><input id=pass type=password placeholder="Admin password"><button class=primary style="margin-top:10px;width:100%" onclick=login()>Enter</button><p id=err class=danger></p></div></div>`;$('pass').addEventListener('keydown',e=>{if(e.key==='Enter')login()})}
async function login(){try{let x=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('pass').value})});if(!x.ok)throw Error('Invalid credentials');let d=await x.json();token=d.token;localStorage.rayone_token=token;appUI()}catch(e){$('err').textContent=e.message}}
function logout(reload=true){token='';localStorage.removeItem('rayone_token');if(reload)loginUI()}
function appUI(){root.innerHTML=`<header><div class=logo>RAYONE AI</div><span class=pill><span class=status></span>CORE ONLINE</span><span class=muted>Universal AI Assistant</span><span class=spacer></span><button class=secondary onclick=voiceToggle()>Voice</button><button class=secondary onclick=logout()>Logout</button></header><div class=layout><nav><button class=active onclick=show('home',this)>⌂ Home</button><button onclick=show('chat',this)>◉ AI Chat</button><button onclick=show('projects',this)>▦ Projects</button><button onclick=show('providers',this)>◉ Providers</button><button onclick=show('models',this)>◆ Models</button><button onclick=show('tools',this)>⚙ Tools</button><button onclick=show('agents',this)>◈ Agents</button><button onclick=show('workflows',this)>↯ Workflows</button><button onclick=show('memory',this)>◆ Memory</button><button onclick=show('jobs',this)>▤ Jobs</button><button onclick=show('admin',this)>▣ Admin</button></nav><main class=main>
<section id=home class="section active"><h1>Command Center</h1><p class=muted>One core. One control plane. Portable execution.</p><div class=grid id=summary></div><div class=card style="margin-top:14px"><h3>Quick Command</h3><div class=row><input id=quick placeholder="Ask RAYONE…"><button class=primary onclick=quickChat()>Run</button></div></div></section>
<section id=chat class=section><h1>AI Chat</h1><div class=card><div id=messages class=messages></div><div class=row><input id=chatin placeholder="Message RAYONE…"><button class=primary onclick=sendChat()>Send</button></div></div></section>
<section id=projects class=section><h1>Projects</h1><div class=card><div class=row><input id=pname placeholder="Project name"><button class=primary onclick=addProject()>Add</button></div><div id=plist class=list></div></div></section>
<section id=providers class=section><h1>Providers</h1><div class=card><div class=two><input id=prname placeholder="Name"><input id=prurl placeholder="Base URL, e.g. http://localhost:11434/v1"><input id=prkey type=password placeholder="API key (optional)"><select id=prkind><option value=openai_compatible>OpenAI Compatible</option><option value=ollama>Ollama</option><option value=lmstudio>LM Studio</option></select></div><div class=toolbar><button class=primary onclick=addProvider()>Add Provider</button><button class=secondary onclick=connectivity()>Test Connectivity</button></div><div id=prlist class=list></div></div></section>
<section id=models class=section><h1>Models</h1><div class=card><div class=two><input id=mn placeholder="Display name"><input id=mm placeholder="Model identifier"><select id=mp></select></div><div class=toolbar><button class=primary onclick=addModel()>Add Model</button></div><div id=modlist class=list></div></div></section>
<section id=tools class=section><h1>Tools</h1><div class=card><div id=tlist class=list></div></div></section>
<section id=agents class=section><h1>Agents</h1><div class=card><div class=two><input id=an placeholder="Agent name"><input id=ad placeholder="Description"><select id=am></select></div><div class=toolbar><button class=primary onclick=addAgent()>Create Agent</button></div><div id=alist class=list></div></div></section>
<section id=workflows class=section><h1>Workflows</h1><div class=card><div class=two><input id=wn placeholder="Workflow name"><textarea id=ws placeholder='Steps JSON, e.g. [{"type":"tool","name":"core.echo","args":{"text":"hello"}}]'></textarea></div><div class=toolbar><button class=primary onclick=addWorkflow()>Create Workflow</button></div><div id=wlist class=list></div></div></section>
<section id=memory class=section><h1>Memory</h1><div class=card><div class=row><input id=mtext placeholder="Save memory"><button class=primary onclick=saveMemory()>Save</button></div><div id=mlist class=list></div></div></section>
<section id=jobs class=section><h1>Jobs</h1><div class=card><div id=jlist class=list></div></div></section>
<section id=admin class=section><h1>Universal Admin</h1><div class=grid><div class=card><b>Security</b><div class=muted>Bearer sessions + encrypted secrets</div></div><div class=card><b>Recovery</b><div class=muted>Checkpoint + restore + export/import</div></div><div class=card><b>Connectivity</b><div class=muted>Providers + tools + agents + workflows</div></div><div class=card><b>Execution</b><div class=muted>Jobs + audit + events</div></div></div><div class=card style="margin-top:14px"><div class=toolbar><button class=primary onclick=checkpoint()>Create Checkpoint</button><button class=secondary onclick=restoreLast()>Restore Last Checkpoint</button><button class=secondary onclick=downloadState()>Export State</button><button class=secondary onclick=showLogs()>Audit / Events</button></div><pre id=logs class=code></pre></div></section>
</main></div>`;loadSummary()}
function show(id,b){document.querySelectorAll('.section').forEach(x=>x.classList.remove('active'));$(id).classList.add('active');document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));b.classList.add('active');load(id)}
async function load(id){try{if(id==='home')loadSummary();if(id==='projects')loadProjects();if(id==='providers')loadProviders();if(id==='models')loadModels();if(id==='tools')loadList('tlist','/api/tools');if(id==='agents')loadAgents();if(id==='workflows')loadWorkflows();if(id==='memory')loadList('mlist','/api/memory/search?q=');if(id==='jobs')loadList('jlist','/api/jobs');}catch(e){alert(e.message)}}
async function loadSummary(){let x=await api('/api/summary');$('summary').innerHTML=Object.entries(x).map(([k,v])=>`<div class=card><div class=muted>${esc(k)}</div><div class=num>${v}</div></div>`).join('')}
async function loadProjects(){let x=await api('/api/projects');$('plist').innerHTML=x.map(p=>`<div class=item><b>${esc(p.name)}</b><div class=muted>${esc(p.type)} · ${esc(p.status)}</div></div>`).join('')||'<div class=muted>No projects.</div>'}
async function loadProviders(){let x=await api('/api/providers');$('prlist').innerHTML=x.map(p=>`<div class=item><b>${esc(p.name)}</b><div class=muted>${esc(p.kind)} · ${esc(p.base_url||'local')} · ${p.enabled?'enabled':'disabled'}</div></div>`).join('')||'<div class=muted>No providers.</div>'}
async function loadModels(){let p=await api('/api/providers'),m=await api('/api/models');$('mp').innerHTML='<option value="">No provider</option>'+p.map(x=>`<option value=${x.id}>${esc(x.name)}</option>`).join('');$('modlist').innerHTML=m.map(x=>`<div class=item><b>${esc(x.name)}</b><div class=muted>${esc(x.model)} · ${esc(x.provider_id||'none')}</div></div>`).join('')||'<div class=muted>No models.</div>'}
async function loadAgents(){let m=await api('/api/models'),a=await api('/api/agents');$('am').innerHTML='<option value="">Auto model</option>'+m.map(x=>`<option value=${x.id}>${esc(x.name)}</option>`).join('');$('alist').innerHTML=a.map(x=>`<div class=item><b>${esc(x.name)}</b><div class=muted>${esc(x.description)}</div></div>`).join('')||'<div class=muted>No agents.</div>'}
async function loadWorkflows(){let x=await api('/api/workflows');$('wlist').innerHTML=x.map(w=>`<div class=item><b>${esc(w.name)}</b><div class=muted>v${w.version} · ${w.enabled?'enabled':'disabled'} · ${w.steps.length} steps</div><button class=secondary onclick="runW('${w.id}')">Run</button></div>`).join('')||'<div class=muted>No workflows.</div>'}
async function loadList(id,u){let x=await api(u);$(''+id).innerHTML=x.length?x.map(p=>`<div class=item><b>${esc(p.name)}</b><div class=muted>${esc(p.description||p.kind||p.status||p.content||'')}</div></div>`).join(''):'<div class=muted>No records yet.</div>'}
async function sendChat(){let v=$('chatin').value.trim();if(!v)return;$('messages').innerHTML+=`<div class="msg you">${esc(v)}</div>`;$('chatin').value='';let x=await api('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:v})});$('messages').innerHTML+=`<div class=msg>${esc(x.answer)}<div class=muted>${esc(x.mode)}</div></div>`;speak(x.answer)}
function quickChat(){$('chatin').value=$('quick').value;show('chat',document.querySelectorAll('nav button')[1]);sendChat()}
async function addProject(){let n=$('pname').value.trim();if(!n)return;await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})});$('pname').value='';loadProjects();loadSummary()}
async function addProvider(){await api('/api/providers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('prname').value,base_url:$('prurl').value,api_key:$('prkey').value,kind:$('prkind').value})});$('prname').value='';$('prurl').value='';$('prkey').value='';loadProviders();loadSummary()}
async function addModel(){await api('/api/models',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('mn').value,model:$('mm').value,provider_id:$('mp').value||null})});$('mn').value='';$('mm').value='';loadModels();loadSummary()}
async function addAgent(){await api('/api/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('an').value,description:$('ad').value,model_id:$('am').value||null})});$('an').value='';$('ad').value='';loadAgents();loadSummary()}
async function addWorkflow(){let steps=[];try{steps=JSON.parse($('ws').value||'[]')}catch(e){return alert('Invalid steps JSON')}await api('/api/workflows',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('wn').value,steps})});$('wn').value='';$('ws').value='';loadWorkflows();loadSummary()}
async function runW(id){let x=await api('/api/workflows/'+id+'/run',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});alert(JSON.stringify(x,null,2))}
async function saveMemory(){let v=$('mtext').value.trim();if(!v)return;await api('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:v})});$('mtext').value='';loadList('mlist','/api/memory/search?q=');loadSummary()}
async function checkpoint(){let x=await api('/api/checkpoints?target=system',{method:'POST'});alert('Checkpoint: '+x.id)}
async function restoreLast(){let x=await api('/api/checkpoints');if(!x.length)return alert('No checkpoint');if(confirm('Restore '+x[0].id+'?')){await api('/api/checkpoints/'+x[0].id+'/restore',{method:'POST'});alert('Restored')}}
async function downloadState(){let x=await api('/api/export');let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(x,null,2)],{type:'application/json'}));a.download='rayone-state.json';a.click()}
async function showLogs(){let a=await api('/api/audits?limit=100'),e=await api('/api/events?limit=100');$('logs').textContent=JSON.stringify({audits:a,events:e},null,2)}
async function connectivity(){alert(JSON.stringify(await api('/api/connectivity'),null,2))}
let recognition=null;function voiceToggle(){if(!('webkitSpeechRecognition'in window||'SpeechRecognition'in window))return alert('Browser speech recognition is not available.');let R=window.SpeechRecognition||window.webkitSpeechRecognition;recognition=new R();recognition.lang='en-IN';recognition.onresult=e=>{$('chatin').value=e.results[0][0].transcript;sendChat()};recognition.start()};function speak(t){if('speechSynthesis'in window){speechSynthesis.cancel();let u=new SpeechSynthesisUtterance(t);u.lang='en-IN';speechSynthesis.speak(u)}}
if(token){api('/api/health').then(appUI).catch(loginUI)}else loginUI();
</script></body></html>'''

@app.get("/",response_class=HTMLResponse)
def ui():return HTML

@app.on_event("startup")
async def startup():asyncio.create_task(worker_loop())
