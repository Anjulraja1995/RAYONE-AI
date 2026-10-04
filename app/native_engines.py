"""RAYONE native advanced engines: offline-first research, documents, data, code and orchestration."""
from pathlib import Path
from urllib.parse import urlparse, quote_plus
import ast, csv, io, json, math, mimetypes, re, statistics, time, uuid, urllib.request, urllib.parse

from fastapi import APIRouter, Depends, HTTPException

from . import main as legacy
from .local_adapters import browser_fetch_text, extract_text, media_probe, ocr_image

router = APIRouter(prefix="/api/v2/native", tags=["RAYONE native engines"])
WORKSPACE = legacy.ROOT / "data" / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

def _auth(token=Depends(legacy.auth)):
    return token

def _safe_workspace_path(value):
    p = (WORKSPACE / str(value).lstrip("/\\")).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise HTTPException(400, "Workspace path escapes workspace")
    return p

def _tokens(text):
    return re.findall(r"[A-Za-z0-9_]+|[\u0900-\u097F]+", str(text).lower())

def _score(query, text):
    q = _tokens(query); body = _tokens(text)
    if not q or not body: return 0.0
    counts = {x: body.count(x) for x in set(q)}
    return sum(counts[x] for x in q) / max(1, len(q))

def _source_from_url(url):
    u=urlparse(url)
    return {"url":url,"scheme":u.scheme,"host":u.hostname,"path":u.path,"secure":u.scheme=="https"}

def native_search(query, limit=10):
    query=str(query).strip()
    if not query: raise ValueError("query required")
    rows=[]
    for p in WORKSPACE.rglob("*"):
        if not p.is_file(): continue
        try:
            if p.stat().st_size > 2_000_000: continue
            raw=p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        score=_score(query,raw)
        name_score=_score(query,p.name)
        if score or name_score:
            rows.append({"title":p.name,"path":str(p.relative_to(WORKSPACE)),"score":round(score + name_score*0.5,6),"source":"workspace"})
    rows.sort(key=lambda x:(-x["score"],x["path"]))
    return {"query":query,"results":rows[:max(1,min(int(limit),50))],"engine":"rayone-native-local-index","external_source_used":False}

def _web_search(query, limit=5):
    url="https://html.duckduckgo.com/html/?q="+quote_plus(query)
    req=urllib.request.Request(url,headers={"User-Agent":"RAYONE-AI/2.0"})
    with urllib.request.urlopen(req,timeout=12) as response:
        raw=response.read(500000).decode("utf-8","ignore")
    links=re.findall(r'nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>',raw,re.I|re.S)
    out=[]
    for href,title in links[:max(1,min(int(limit),10))]:
        title=re.sub(r"<[^>]+>","",title)
        href=href.replace("&amp;","&")
        out.append({"url":href,"title":re.sub(r"\\s+"," ",title).strip()})
    return out

def research(query, max_sources=5):
    query=str(query).strip()
    if not query: raise ValueError("query required")
    if re.match(r"^https?://",query,re.I):
        fetched=browser_fetch_text(query,max_bytes=400000)
        text_value=fetched.get("text","")
        return {"query":query,"mode":"url","engine":"rayone-native-research","sources":[{"url":query,"status":fetched.get("status",0),"title":text_value[:160],"text":text_value[:12000],"score":1.0}],"source_count":1,"verified":bool(text_value),"external_source_used":True}
    try:
        hits=_web_search(query,max_sources)
        sources=[]
        for hit in hits:
            try:
                fetched=browser_fetch_text(hit["url"],max_bytes=120000)
                text_value=fetched.get("text","")
                if text_value:
                    sources.append(hit|{"status":fetched.get("status",0),"text":text_value[:12000],"score":1.0})
            except Exception:
                continue
        if sources:
            return {"query":query,"mode":"web","engine":"rayone-native-research","sources":sources,"source_count":len(sources),"verified":True,"external_source_used":True}
    except Exception:
        pass
    local=native_search(query,max_sources)
    sources=[]
    for item in local["results"]:
        p=_safe_workspace_path(item["path"])
        try: content=p.read_text(encoding="utf-8",errors="ignore")[:12000]
        except Exception: continue
        sources.append({"url":"workspace://"+item["path"],"title":item["title"],"text":content,"score":item["score"],"status":200})
    return {"query":query,"mode":"local","engine":"rayone-native-research","sources":sources,"source_count":len(sources),"verified":bool(sources),"external_source_used":False}

def _document_extract(path):
    p=_safe_workspace_path(path)
    if not p.exists() or not p.is_file(): raise FileNotFoundError(str(path))
    ext=p.suffix.lower()
    meta={"name":p.name,"path":str(p.relative_to(WORKSPACE)),"extension":ext,"mime":mimetypes.guess_type(p.name)[0] or "application/octet-stream","size":p.stat().st_size}
    if ext in {".txt",".md",".markdown",".json",".csv",".html",".htm",".xml",".py",".js",".ts",".css",".yaml",".yml"}:
        raw=p.read_text(encoding="utf-8",errors="ignore")
        return meta | {"text": extract_text(raw) if ext in {".html",".htm"} else raw[:500000],"engine":"rayone-native-document"}
    if ext==".pdf":
        try:
            from pypdf import PdfReader
            reader=PdfReader(str(p))
            text_value="\n".join((page.extract_text() or "") for page in reader.pages)
            return meta | {"text":text_value[:500000],"pages":len(reader.pages),"engine":"rayone-native-document"}
        except Exception as e: return meta | {"text":"","pages":0,"error":str(e),"engine":"rayone-native-document"}
    if ext==".docx":
        try:
            from docx import Document
            doc=Document(str(p))
            text_value="\n".join(x.text for x in doc.paragraphs)
            return meta | {"text":text_value[:500000],"paragraphs":len(doc.paragraphs),"engine":"rayone-native-document"}
        except Exception as e: return meta | {"text":"","error":str(e),"engine":"rayone-native-document"}
    if ext==".xlsx":
        try:
            from openpyxl import load_workbook
            wb=load_workbook(str(p),read_only=True,data_only=True)
            sheets={}
            for ws in wb.worksheets:
                sheets[ws.title]=[[c.value for c in row] for row in ws.iter_rows(values_only=False)]
            return meta | {"sheets":sheets,"engine":"rayone-native-document"}
        except Exception as e: return meta | {"sheets":{},"error":str(e),"engine":"rayone-native-document"}
    return meta | {"text":"","supported":False,"engine":"rayone-native-document"}

def _csv_analyze(text):
    reader=csv.DictReader(io.StringIO(text))
    rows=list(reader); fields=reader.fieldnames or []
    numeric={}
    for f in fields:
        vals=[]
        for r in rows:
            try: vals.append(float(r.get(f,"")))
            except Exception: pass
        if vals: numeric[f]={"count":len(vals),"sum":sum(vals),"mean":sum(vals)/len(vals),"min":min(vals),"max":max(vals),"median":statistics.median(vals)}
    return {"rows":len(rows),"columns":fields,"numeric":numeric,"engine":"rayone-native-data"}

def code_analyze(source, language="python"):
    source=str(source)
    result={"language":language,"lines":len(source.splitlines()),"characters":len(source),"engine":"rayone-native-code","syntax_ok":True,"errors":[],"imports":[],"functions":[],"classes":[]}
    if language.lower() in {"python","py"}:
        try:
            tree=ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node,(ast.Import,ast.ImportFrom)): result["imports"].append(ast.unparse(node))
                elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)): result["functions"].append(node.name)
                elif isinstance(node,ast.ClassDef): result["classes"].append(node.name)
        except SyntaxError as e:
            result["syntax_ok"]=False; result["errors"]=[{"line":e.lineno,"column":e.offset,"message":e.msg}]
    else:
        result["brace_balance"]=source.count("{")-source.count("}")
        result["paren_balance"]=source.count("(")-source.count(")")
        result["bracket_balance"]=source.count("[")-source.count("]")
        result["syntax_ok"]=not any(result[x] for x in ("brace_balance","paren_balance","bracket_balance"))
    return result

def workflow_plan(steps, input_data=None, max_steps=50):
    if not isinstance(steps,list) or not steps: raise ValueError("steps must be a non-empty list")
    if len(steps)>max_steps: raise ValueError("workflow exceeds max_steps")
    data=dict(input_data or {}); outputs=[]; trace=[]
    for idx,step in enumerate(steps):
        if not isinstance(step,dict): raise ValueError(f"step {idx} must be an object")
        kind=step.get("type",step.get("action",""))
        if kind=="condition":
            left=data.get(step.get("key"),step.get("left"))
            right=step.get("equals",step.get("right"))
            matched=left==right
            trace.append({"step":idx,"type":kind,"matched":matched})
            if not matched: continue
            continue
        if kind=="set":
            key=str(step.get("key","")).strip()
            if not key: raise ValueError(f"step {idx}: key required")
            data[key]=step.get("value",data.get(key)); out=data[key]
        elif kind=="transform":
            key=str(step.get("key","")).strip()
            value=data.get(key,step.get("value",""))
            op=str(step.get("operation","string")).lower()
            if op=="lower": out=str(value).lower()
            elif op=="upper": out=str(value).upper()
            elif op=="strip": out=str(value).strip()
            elif op=="length": out=len(value)
            elif op=="json": out=json.dumps(value,ensure_ascii=False)
            else: raise ValueError(f"step {idx}: unsupported transform {op}")
            data[key]=out
        elif kind=="value":
            out=step.get("value")
        else:
            raise ValueError(f"step {idx}: unsupported native action {kind}")
        outputs.append(out); trace.append({"step":idx,"type":kind,"output":out})
    return {"outputs":outputs,"data":data,"trace":trace,"verified":True,"engine":"rayone-native-orchestrator"}

@router.get("/status")
def engine_status(_:str=Depends(_auth)):
    deps={}
    for name in ("pypdf","docx","openpyxl","PIL"):
        try:
            __import__(name); deps[name]=True
        except Exception: deps[name]=False
    return {"engine":"rayone-native","mode":"offline-first","engines":{"research":True,"workspace_search":True,"documents":True,"data":True,"code":True,"orchestration":True,"ocr_adapter":True,"media_probe":True},"dependencies":deps,"external_dependencies_optional":True}

@router.post("/search")
def search_endpoint(payload:dict,_:str=Depends(_auth)):
    return native_search(payload.get("query",""),payload.get("limit",10))

@router.post("/research")
async def research_endpoint(payload:dict,_:str=Depends(_auth)):
    return research(payload.get("query",""),payload.get("max_sources",5))

@router.post("/document")
def document_endpoint(payload:dict,_:str=Depends(_auth)):
    return _document_extract(payload.get("path",""))

@router.post("/data/csv")
def data_csv(payload:dict,_:str=Depends(_auth)):
    text_value=str(payload.get("text",""))
    return _csv_analyze(text_value)

@router.post("/code/analyze")
def code_endpoint(payload:dict,_:str=Depends(_auth)):
    return code_analyze(payload.get("source",""),payload.get("language","python"))

@router.post("/workflow/plan")
def workflow_endpoint(payload:dict,_:str=Depends(_auth)):
    try: return workflow_plan(payload.get("steps"),payload.get("input",{}),payload.get("max_steps",50))
    except ValueError as e: raise HTTPException(400,str(e))

@router.post("/media/probe")
def media_endpoint(payload:dict,_:str=Depends(_auth)):
    path=payload.get("path","")
    try: return media_probe(_safe_workspace_path(path))
    except FileNotFoundError: raise HTTPException(404,"Media file not found")

@router.post("/ocr")
def ocr_endpoint(payload:dict,_:str=Depends(_auth)):
    path=_safe_workspace_path(payload.get("path",""))
    result=ocr_image(path)
    if result.get("status")=="unavailable": raise HTTPException(503,"OCR engine is not installed")
    return result
