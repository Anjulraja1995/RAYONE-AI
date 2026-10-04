from fastapi import FastAPI, HTTPException, Header, Depends, Query, Request
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
ADMIN_PASSWORD = os.getenv("RAYONE_ADMIN_PASSWORD", "RAYONE-"+"Admin-2026")
SESSION_TTL = max(300, min(int(os.getenv("RAYONE_SESSION_TTL", "86400")), 604800))
LOGIN_WINDOW = max(30, int(os.getenv("RAYONE_LOGIN_WINDOW", "300")))
LOGIN_MAX_FAILURES = max(1, int(os.getenv("RAYONE_LOGIN_MAX_FAILURES", "5")))
LOGIN_LOCKOUT = max(10, int(os.getenv("RAYONE_LOGIN_LOCKOUT", "60")))
_login_failures = {}
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

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()

def verify_password(password: str, stored: str) -> bool:
    if not stored.startswith("scrypt$"):
        return secrets.compare_digest(password, stored)
    try:
        _, n, r, p, salt_b64, digest_b64 = stored.split("$", 5)
        salt=base64.urlsafe_b64decode(salt_b64.encode())
        expected=base64.urlsafe_b64decode(digest_b64.encode())
        actual=hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p))
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False

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
class Chat(BaseModel): message: str = Field(min_length=1); project_id: str|None = None; agent_id: str|None = None; model_id: str|None = None; request_id: str|None = None
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

def _login_key(request: Request) -> str:
    host = request.client.host if request.client else "local"
    return hashlib.sha256(host.encode()).hexdigest()[:24]

def _login_locked(key: str) -> bool:
    item = _login_failures.get(key)
    if not item: return False
    if now() >= item["lock_until"]:
        _login_failures.pop(key, None)
        return False
    return True

def _record_login_failure(key: str):
    t = now()
    item = _login_failures.get(key)
    if not item or t - item["window_start"] > LOGIN_WINDOW:
        item = {"window_start": t, "failures": 0, "lock_until": 0}
    item["failures"] += 1
    if item["failures"] >= LOGIN_MAX_FAILURES:
        item["lock_until"] = t + LOGIN_LOCKOUT
    _login_failures[key] = item

def _clear_login_failures(key: str):
    _login_failures.pop(key, None)

@app.post("/api/auth/login")
def login(x: LoginIn, request: Request):
    key = _login_key(request)
    if _login_locked(key):
        audit("login_blocked", "auth", {})
        raise HTTPException(429, "Too many failed login attempts; try again later")
    execute("delete from sessions where expires<=?", (now(),))
    override = one("SELECT value FROM settings WHERE key=?", ("admin_password_hash",))
    valid = verify_password(x.password, override["value"]) if override else secrets.compare_digest(x.password, ADMIN_PASSWORD)
    if not valid:
        _record_login_failure(key)
        audit("login_failed", "auth", {})
        raise HTTPException(401, "Invalid credentials")
    _clear_login_failures(key)
    token = secrets.token_urlsafe(32)
    expires = now() + SESSION_TTL
    execute("INSERT INTO sessions VALUES(?,?,?)", (token, now(), expires))
    audit("login", "auth", {"expires": expires})
    return {"token": token, "expires_in": SESSION_TTL, "expires_at": expires}

@app.post("/api/auth/logout")
def logout(authorization: str|None = Header(default=None)):
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    if token:
        execute("DELETE FROM sessions WHERE token=?", (token,))
        audit("logout", "auth", {})
    return {"ok": True}

def auth(authorization: str|None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization[7:].strip()
    if not token or len(token) > 256:
        raise HTTPException(401, "Invalid session token")
    s = one("SELECT * FROM sessions WHERE token=? AND expires>?", (token, now()))
    if not s:
        execute("delete from sessions where token=?", (token,))
        raise HTTPException(401, "Session expired or invalid")
    return token

@app.get("/api/auth/session")
def session_info(token: str = Depends(auth)):
    s = one("select created,expires from sessions where token=?", (token,))
    if not s: raise HTTPException(401, "Session expired or invalid")
    return {"authenticated": True, "created_at": s["created"], "expires_at": s["expires"], "expires_in": max(0, int(s["expires"] - now()))}

@app.get("/api/health")
def health():
    try:
        db_ok = bool(one("select 1 as ok"))
        db_integrity = one("pragma integrity_check")["integrity_check"] == "ok"
    except Exception:
        db_ok = False
        db_integrity = False
    active_sessions = one("select count(*) as n from sessions where expires>?", (now(),))["n"] if db_ok else 0
    return {"ok": db_ok and db_integrity, "name": "RAYONE AI", "version": APP_VERSION, "database": "sqlite", "database_integrity": db_integrity, "active_sessions": active_sessions, "mode": "free-local-first", "authenticated_admin": True}

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

# Built-in zero-cost tool pack. Every entry below is locally executable and has no paid-service dependency.
from .local_tools import build_builtin_pack, TOTAL_CAPABILITIES
from .native_creative import generate_image, generate_design, generate_audio, generate_music, generate_voice, generate_video

BUILTIN_PACK = build_builtin_pack()
BUILTIN_PACK.update({
    "image": {"generate": generate_image},
    "design": {"generate": generate_design},
    "audio": {"generate": generate_audio},
    "music": {"generate": generate_music},
    "voice": {"generate": generate_voice},
    "video": {"generate": generate_video},
})
NATIVE_CREATIVE_CAPABILITIES = 6
TOTAL_CAPABILITIES = TOTAL_CAPABILITIES + NATIVE_CREATIVE_CAPABILITIES


def register_builtin_pack():
    t=now()
    for family,ops in BUILTIN_PACK.items():
        for op in ops:
            tool_id=f"local.{family}.{op}"
            execute("insert or ignore into tools values(?,?,?,?,?,?,?,?)",(tool_id,f"{family}.{op}",f"Local zero-cost {family} tool: {op}","builtin",1,dumps({"family":family,"operation":op}),t,t))

register_builtin_pack()

async def execute_tool_internal(name,args):
    tool=one("select * from tools where id=? and enabled=1",(name,))
    if not tool:raise HTTPException(404,"Tool is not registered or enabled")
    if name=="core.echo":result=args.get("text","")
    elif name=="core.calculator":result=calc(str(args.get("expression","")))
    elif name=="core.datetime":result=time.strftime("%Y-%m-%d %H:%M:%S %Z",time.localtime())
    elif name=="core.memory_search":result=rows("select * from memories where content like ? order by created desc limit 20",(f"%{args.get('query','')}%",))
    elif name=="core.json":result=json.loads(args.get("value","{}"))
    elif name.startswith("local."):
        parts=name.split(".",2)
        family,op=parts[1],parts[2]
        fn=BUILTIN_PACK.get(family,{}).get(op)
        if not fn: raise HTTPException(404,"Local tool operation not found")
        result=fn(args)
    elif tool["kind"]=="http":
        cfg=json.loads(tool["config"] or "{}");url=cfg.get("url","")
        if not re.match(r"^https?://",url):raise ValueError("Tool URL must be http(s)")
        async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:r=await client.get(url,params=args.get("params",{}));result={"status":r.status_code,"text":r.text[:10000]}
    else: raise HTTPException(400,"Unsupported tool kind")
    emit("tool.completed",{"tool":name});audit("execute","tool",{"name":name});return result

@app.post("/api/tools/execute")
async def execute_tool(x:ToolCall,_:str=Depends(auth)):
    from .execution_pipeline import run_pipeline
    result=await run_pipeline(message="",kind="tool",target=x.name,args=x.args)
    return {"ok":True,"tool":x.name,"result":result.get("result"),"request_id":result.get("request_id"),"state":result.get("state")}

async def provider_chat(message, model_id=None):
    """Route chat through configured providers with ordered failover.
    No provider is mandatory: execution_pipeline supplies the local fallback.
    """
    from .provider_adapters import chat as adapter_chat, ProviderError
    candidates=[]
    if model_id:
        m=one("select * from models where id=? and enabled=1",(model_id,))
        p=one("select * from providers where id=? and enabled=1",(m["provider_id"],)) if m else None
        if m and p: candidates=[(p,m)]
    else:
        rows_cfg=rows("""select p.*,m.id as model_id,m.model as selected_model
                         from providers p left join models m
                         on m.provider_id=p.id and m.enabled=1
                         where p.enabled=1 order by p.rowid,m.rowid""")
        for p0 in rows_cfg:
            m0={"model":p0.get("selected_model") or json.loads(p0["config"] or "{}").get("model","")}
            candidates.append((p0,m0))
    attempts=[]
    for p,m in candidates:
        if not p.get("base_url"): continue
        cfg=json.loads(p["config"] or "{}"); token=""
        if cfg.get("api_key"):
            try: token=FERNET.decrypt(cfg["api_key"].encode()).decode()
            except InvalidToken: token=""
        model=m.get("model") or cfg.get("model","")
        try:
            answer=await adapter_chat(p["base_url"],p.get("kind","openai_compatible"),model,message,token)
            emit("provider.completed",{"provider":p["name"],"model":model})
            return answer,p["name"]
        except Exception as ex:
            attempts.append({"provider":p["name"],"error":str(ex)})
            audit("provider.failover","provider",{"name":p["name"],"error":str(ex)})
            continue
    return None,None

@app.post("/api/chat")
async def chat(x:Chat,_:str=Depends(auth)):
    from .execution_pipeline import run_pipeline
    result=await run_pipeline(message=x.message,model_id=x.model_id,request_id=x.request_id)
    if result.get("intent")=="tool":
        return {"mode":"tool","tool":result.get("target","core.calculator"),"answer":str(result.get("result")),"result":result.get("result"),"request_id":result.get("request_id")}
    return {"mode":"provider" if result.get("provider") not in {None,"local"} else "local",
            "provider":result.get("provider"),"answer":result.get("answer") or str(result.get("result","")),
            "request_id":result.get("request_id"),"state":result.get("state")}

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
                    from .execution_pipeline import run_pipeline
                    if j["type"]=="chat":
                        res=await run_pipeline(message=inp.get("message",""),model_id=inp.get("model_id"),request_id=j["id"])
                    elif j["type"]=="tool":
                        res=await run_pipeline(message="",kind="tool",target=inp["name"],args=inp.get("args",{}),request_id=j["id"])
                    elif j["type"]=="workflow":
                        res=await run_pipeline(message="",kind="workflow",target=inp["workflow_id"],args=inp.get("input",{}),request_id=j["id"])
                    else:
                        raise ValueError("Unknown job type")
                    status="completed" if res.get("state")=="Complete" else "failed"
                    execute("update jobs set status=?,result=?,error=?,updated=? where id=?",(status,dumps(res),None if status=="completed" else str(res),now(),j["id"]))
                    emit("job.completed" if status=="completed" else "job.failed",{"id":j["id"]})
                except Exception as e:
                    execute("update jobs set status='failed',error=?,updated=? where id=?",(str(e),now(),j["id"]))
                    emit("job.failed",{"id":j["id"],"error":str(e)})
            else:
                await asyncio.sleep(.4)
        except Exception:
            await asyncio.sleep(1)

async def run_workflow_internal(wid, input_data):
    w=one("select * from workflows where id=? and enabled=1",(wid,));
    if not w:raise ValueError("Workflow not found or disabled")
    data=input_data.copy() if isinstance(input_data,dict) else {"input":input_data};outputs=[]
    for step in json.loads(w["steps"]):
        kind=step.get("type",step.get("action",""))
        if kind=="tool":out=await execute_tool_internal(step["name"],step.get("args",data))
        elif kind=="chat":out=(await provider_chat(str(step.get("message",data.get("message",data)))))[0] or f"RAYONE local core received: {data}"
        elif kind=="memory":out=execute("insert into memories values(?,?,?,?,?)",(str(uuid.uuid4()),step.get("scope","workflow"),str(step.get("content",data)),dumps(step.get("metadata",{})),now()))
        elif kind=="value":out=step.get("value",data)
        elif kind=="set":
            key=str(step.get("key","")).strip()
            if not key: raise ValueError("Workflow set step requires key")
            data[key]=step.get("value",data.get(key)); out=data[key]
        elif kind=="transform":
            key=str(step.get("key","")).strip()
            if not key: raise ValueError("Workflow transform step requires key")
            value=data.get(key,data.get("previous",data))
            operation=str(step.get("operation","string")).lower()
            if operation=="lower": out=str(value).lower()
            elif operation=="upper": out=str(value).upper()
            elif operation=="strip": out=str(value).strip()
            elif operation=="length": out=len(value)
            else: raise ValueError("Unsupported transform operation")
            data[key]=out
        else: raise ValueError("Unsupported workflow action: "+kind)
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

HTML=(ROOT / 'app' / 'dashboard.html').read_text(encoding='utf-8')


@app.get("/",response_class=HTMLResponse)
def ui():return HTML

@app.on_event("startup")
async def startup():asyncio.create_task(worker_loop())


# RAYONE v2 universal control plane and VORQYON execution layer
from .advanced import router as advanced_router, scheduler_loop
from .native_engines import router as native_engines_router
app.include_router(advanced_router)
app.include_router(native_engines_router)\nfrom .production_routes import router as production_router\napp.include_router(production_router)

@app.on_event("startup")
async def advanced_startup():
    asyncio.create_task(scheduler_loop())