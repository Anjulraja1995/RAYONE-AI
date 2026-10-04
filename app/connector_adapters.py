"""Credential-gated, provider-neutral connector execution."""
from __future__ import annotations
import json, re
from urllib.parse import urljoin
import httpx

class ConnectorError(RuntimeError): pass

def _allowed_url(url:str)->bool:
    return bool(re.match(r"^https?://",url or "",re.I))

def _headers(config:dict)->dict:
    out={"User-Agent":"RAYONE-AI/2.0"}
    token=config.get("token") or config.get("api_key") or ""
    auth=config.get("auth","bearer")
    if token:
        if auth=="basic":
            out["Authorization"]="Basic "+str(token)
        else:
            out["Authorization"]="Bearer "+str(token)
    extra=config.get("headers") or {}
    if isinstance(extra,dict): out.update({str(k):str(v) for k,v in extra.items()})
    return out

async def execute(base_url:str, kind:str, config:dict, method:str="GET",
                  path:str="", params:dict|None=None, body:dict|None=None, timeout:float=20)->dict:
    base=base_url.rstrip("/")+"/"
    target=urljoin(base,str(path).lstrip("/"))
    if not _allowed_url(target): raise ConnectorError("Connector URL must be http(s)")
    method=method.upper()
    if method not in {"GET","POST","PUT","PATCH","DELETE"}: raise ConnectorError("Unsupported method")
    if method!="GET" and not config.get("allow_mutations",False):
        raise ConnectorError("Connector mutation requires allow_mutations=true")
    async with httpx.AsyncClient(timeout=timeout,follow_redirects=False) as c:
        r=await c.request(method,target,headers=_headers(config),params=params or {},json=body if method!="GET" else None)
    content_type=r.headers.get("content-type","")
    data=r.json() if "application/json" in content_type else r.text[:20000]
    return {"ok":r.status_code < 400,"status":r.status_code,"kind":kind,"method":method,
            "url":target,"data":data}

def normalize_kind(kind:str)->str:
    k=(kind or "http").lower()
    aliases={"rest":"http","webhook":"http","api":"http","openai":"openai_compatible"}
    return aliases.get(k,k)
