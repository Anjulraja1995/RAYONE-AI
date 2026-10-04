"""Production-grade provider adapters for RAYONE AI.

Local-first and provider-neutral. Supports OpenAI-compatible APIs, Ollama and
LM Studio-compatible endpoints without making any external service mandatory.
"""
from __future__ import annotations
import json, time
from typing import Any
import httpx

class ProviderError(RuntimeError):
    pass

def _headers(token: str = "") -> dict[str,str]:
    return {"Authorization": f"Bearer {token}"} if token else {}

def _chat_url(base_url: str, kind: str) -> str:
    base = base_url.rstrip("/")
    k = (kind or "openai_compatible").lower()
    if k in {"ollama"}:
        return base + "/api/chat"
    return base + "/chat/completions"

def _models_url(base_url: str, kind: str) -> str:
    base = base_url.rstrip("/")
    return base + ("/api/tags" if (kind or "").lower()=="ollama" else "/models")

async def health(base_url: str, kind: str, token: str = "", timeout: float = 8) -> dict:
    started=time.perf_counter()
    if not base_url:
        return {"ok":False,"error":"base_url is required","latency_ms":0}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
            r=await c.get(base_url.rstrip("/"),headers=_headers(token))
        return {"ok":r.status_code < 500,"status":r.status_code,
                "latency_ms":round((time.perf_counter()-started)*1000,2)}
    except Exception as e:
        return {"ok":False,"error":str(e),"latency_ms":round((time.perf_counter()-started)*1000,2)}

async def list_models(base_url: str, kind: str, token: str = "", timeout: float = 10) -> dict:
    url=_models_url(base_url,kind)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
        r=await c.get(url,headers=_headers(token))
    if r.status_code >= 400: raise ProviderError(f"model discovery failed: HTTP {r.status_code}")
    data=r.json()
    if (kind or "").lower()=="ollama":
        models=[{"id":x.get("name"),"name":x.get("name"),"raw":x} for x in data.get("models",[])]
    else:
        models=[{"id":x.get("id"),"name":x.get("id"),"raw":x} for x in data.get("data",[])]
    return {"models":[x for x in models if x.get("id")],"provider_kind":kind,"url":url}

async def chat(base_url: str, kind: str, model: str, message: str, token: str = "",
               timeout: float = 45, system: str|None = None) -> str:
    k=(kind or "openai_compatible").lower()
    if not model: raise ProviderError("model is required")
    messages=[]
    if system: messages.append({"role":"system","content":system})
    messages.append({"role":"user","content":message})
    if k=="ollama":
        payload={"model":model,"messages":messages,"stream":False}
    else:
        payload={"model":model,"messages":messages,"temperature":0.2}
    async with httpx.AsyncClient(timeout=timeout,follow_redirects=False) as c:
        r=await c.post(_chat_url(base_url,k),headers=_headers(token),json=payload)
    if r.status_code >= 400:
        raise ProviderError(f"provider returned HTTP {r.status_code}: {r.text[:500]}")
    data=r.json()
    if k=="ollama":
        text=((data.get("message") or {}).get("content") or "").strip()
    else:
        choices=data.get("choices") or []
        text=(((choices[0] if choices else {}).get("message") or {}).get("content") or "").strip()
    if not text: raise ProviderError("provider returned an empty response")
    return text

async def chat_failover(candidates: list[dict], message: str, model_id: str|None=None) -> tuple[str|None,str|None,dict]:
    attempts=[]
    for item in candidates:
        if not item.get("base_url") or not item.get("enabled",1): continue
        model=item.get("model") or item.get("default_model") or ""
        if model_id and item.get("model_id") not in {None,model_id}: continue
        started=time.perf_counter()
        try:
            text=await chat(item["base_url"],item.get("kind","openai_compatible"),model,item.get("message",message),item.get("token",""))
            attempts.append({"provider":item.get("name"),"ok":True,"latency_ms":round((time.perf_counter()-started)*1000,2)})
            return text,item.get("name"),{"attempts":attempts}
        except Exception as e:
            attempts.append({"provider":item.get("name"),"ok":False,"error":str(e),"latency_ms":round((time.perf_counter()-started)*1000,2)})
    return None,None,{"attempts":attempts}
