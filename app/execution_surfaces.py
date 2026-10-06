"""Execution surfaces for RAYONE's next implementation layer.

These routes sit above the existing control plane without replacing its APIs:
Create Studio, Research, Agents, Workflows, Unified Orchestrator and VORQYON.
All runs are persisted, verified and auditable.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from . import main as legacy
from .execution_pipeline import run_pipeline, state_snapshot
from .native_engines import research as native_research

router = APIRouter(prefix="/api/v2", tags=["RAYONE execution surfaces"])

SURFACES = {"studio", "research", "agent", "workflow", "orchestrator", "vorqyon"}
STUDIO_KINDS = {"image", "design", "audio", "music", "voice", "video"}


def _auth(token=Depends(legacy.auth)):
    return token


def _init_store():
    legacy.execute(
        """CREATE TABLE IF NOT EXISTS surface_runs(
            id TEXT PRIMARY KEY,
            surface TEXT NOT NULL,
            target TEXT,
            status TEXT NOT NULL,
            input TEXT NOT NULL,
            output TEXT,
            verification TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL,
            updated REAL NOT NULL
        )"""
    )


_init_store()


def _dump(value: Any) -> str:
    return legacy.dumps(value)


def _row(run_id: str):
    return legacy.one("select * from surface_runs where id=?", (run_id,))


def _persist(run_id: str, **fields):
    allowed = {"surface", "target", "status", "input", "output", "verification", "attempts"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    sets = ["updated=?"]
    args = [legacy.now()]
    for key, value in fields.items():
        sets.append(f"{key}=?")
        args.append(_dump(value) if key in {"input", "output", "verification"} else value)
    args.append(run_id)
    legacy.execute("update surface_runs set " + ",".join(sets) + " where id=?", args)


def _new(surface: str, target: str, payload: dict, run_id: str | None = None) -> str:
    rid = run_id or str(uuid.uuid4())
    now = legacy.now()
    legacy.execute(
        "insert into surface_runs values(?,?,?,?,?,?,?,?,?,?)",
        (rid, surface, target, "Queued", _dump(payload), None, None, 0, now, now),
    )
    return rid


def _verify(value: Any) -> dict:
    if value is None:
        return {"ok": False, "reason": "empty result"}
    if isinstance(value, dict):
        if value.get("status") in {"failed", "error"} or value.get("ok") is False:
            return {"ok": False, "reason": "execution reported failure"}
        if value.get("verified") is False:
            return {"ok": False, "reason": "execution reported unverified"}
        if value.get("size") is not None:
            try:
                if int(value["size"]) <= 0:
                    return {"ok": False, "reason": "artifact is empty"}
            except (TypeError, ValueError):
                return {"ok": False, "reason": "invalid artifact size"}
        return {"ok": True, "checks": ["non-empty", "status", "artifact"]}
    if isinstance(value, (list, tuple, str, bytes)):
        return {"ok": bool(value), "checks": ["non-empty"]}
    return {"ok": True, "checks": ["result-present"]}


def _source_summary(result: dict) -> dict:
    sources = result.get("sources") or []
    points = []
    for source in sources:
        text = re.sub(r"\s+", " ", str(source.get("text", ""))).strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        snippet = " ".join(x for x in sentences[:2] if x).strip()
        if snippet:
            points.append({"title": source.get("title", "Untitled source"), "url": source.get("url", ""), "summary": snippet[:700]})
    return {
        "query": result.get("query"),
        "mode": result.get("mode"),
        "source_count": len(sources),
        "sources": sources,
        "key_points": points,
        "processed": True,
        "verified": bool(result.get("verified")) and bool(sources),
    }


async def _run_studio(payload: dict, run_id: str) -> dict:
    kind = str(payload.get("kind", "")).strip().lower()
    if kind not in STUDIO_KINDS:
        raise HTTPException(400, "Unsupported studio kind")
    data = dict(payload.get("input") or {})
    prompt = str(data.get("prompt", data.get("text", ""))).strip()
    if not prompt:
        raise HTTPException(400, "studio input.prompt is required")
    _persist(run_id, status="Executing", target=kind)
    result = await legacy.execute_tool_internal(f"local.{kind}.generate", data)
    verification = _verify(result)
    if not verification["ok"]:
        raise RuntimeError(verification["reason"])
    legacy.execute(
        "update media_jobs set status=?,output=?,provider=?,updated=? where id=?",
        ("completed", _dump(result), "native", legacy.now(), run_id),
    ) if legacy.one("select id from media_jobs where id=?", (run_id,)) else legacy.execute(
        "insert into media_jobs values(?,?,?,?,?,?,?,?)",
        (run_id, kind, "completed", _dump(data), _dump(result), "native", legacy.now(), legacy.now()),
    )
    return {"run_id": run_id, "surface": "studio", "kind": kind, "status": "Complete", "result": result, "verification": verification}


async def _run_research(payload: dict, run_id: str) -> dict:
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(400, "research query is required")
    _persist(run_id, status="Executing", target="research")
    result = native_research(query, max_sources=max(1, min(int(payload.get("max_sources", 5)), 10)))
    processed = _source_summary(result)
    verification = _verify(processed)
    if not verification["ok"]:
        raise RuntimeError("Research verification failed")
    return {"run_id": run_id, "surface": "research", "status": "Complete", "result": processed, "verification": verification}


async def _run_agent(payload: dict, run_id: str) -> dict:
    agent_id = str(payload.get("agent_id", "")).strip()
    agent = legacy.one("select * from agents where id=? and enabled=1", (agent_id,))
    if not agent:
        raise HTTPException(404, "Agent not found or disabled")
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(400, "agent message is required")
    max_steps = max(1, min(int(payload.get("max_steps", 5)), 10))
    plan = payload.get("steps") or [{"type": "chat", "message": message}]
    if not isinstance(plan, list) or not plan:
        raise HTTPException(400, "steps must be a non-empty list")

    _persist(run_id, status="Executing", target=agent_id)
    history = []
    for step_no, step in enumerate(plan[:max_steps], 1):
        if not isinstance(step, dict):
            raise HTTPException(400, f"step {step_no}: expected object")
        kind = str(step.get("type", step.get("action", ""))).strip()
        child_id = f"{run_id}:{step_no}"
        if kind == "tool":
            name = str(step.get("name", "")).strip()
            if not name:
                raise HTTPException(400, f"step {step_no}: tool name required")
            result = await run_pipeline(message="", kind="tool", target=name, args=step.get("args") or {}, request_id=child_id)
        elif kind == "chat":
            prompt = str(step.get("message", message))
            result = await run_pipeline(message=prompt, model_id=agent["model_id"], request_id=child_id)
        elif kind == "memory":
            content = str(step.get("content", message))
            mid = str(uuid.uuid4())
            legacy.execute(
                "insert into memories values(?,?,?,?,?)",
                (mid, step.get("scope", "agent"), content, _dump(step.get("metadata", {})), legacy.now()),
            )
            result = {"memory_id": mid, "content": content, "verified": True}
        elif kind == "workflow":
            workflow_id = str(step.get("workflow_id", "")).strip()
            if not workflow_id:
                raise HTTPException(400, f"step {step_no}: workflow_id required")
            result = await run_pipeline(
                message="", kind="workflow", target=workflow_id, args=step.get("input") or {}, request_id=child_id
            )
        else:
            raise HTTPException(400, f"step {step_no}: unsupported action '{kind}'")
        verification = _verify(result)
        if not verification["ok"]:
            raise RuntimeError(f"Agent step {step_no} verification failed")
        history.append({"step": step_no, "action": kind, "result": result, "verification": verification})
    result = {"agent_id": agent_id, "steps": history, "step_count": len(history), "verified": True}
    return {"run_id": run_id, "surface": "agent", "status": "Complete", "result": result, "verification": _verify(result)}


async def _run_workflow(payload: dict, run_id: str) -> dict:
    workflow_id = str(payload.get("workflow_id", "")).strip()
    if not workflow_id:
        raise HTTPException(400, "workflow_id is required")
    _persist(run_id, status="Executing", target=workflow_id)
    result = await run_pipeline(
        message="", kind="workflow", target=workflow_id, args=payload.get("input") or {}, request_id=run_id
    )
    verification = _verify(result.get("result", result))
    if not verification["ok"]:
        raise RuntimeError("Workflow verification failed")
    return {"run_id": run_id, "surface": "workflow", "status": "Complete", "result": result, "verification": verification}


async def _dispatch(surface: str, payload: dict, run_id: str) -> dict:
    _persist(run_id, status="Understanding")
    if surface == "studio":
        return await _run_studio(payload, run_id)
    if surface == "research":
        return await _run_research(payload, run_id)
    if surface == "agent":
        return await _run_agent(payload, run_id)
    if surface == "workflow":
        return await _run_workflow(payload, run_id)
    raise HTTPException(400, "Unsupported execution surface")


async def _vorqyon(surface: str, payload: dict, run_id: str, max_attempts: int) -> dict:
    attempts = []
    for attempt in range(1, max_attempts + 1):
        _persist(run_id, status="Executing", attempts=attempt)
        try:
            result = await _dispatch(surface, payload, run_id)
            verification = result.get("verification") or _verify(result.get("result"))
            if not verification.get("ok"):
                raise RuntimeError(verification.get("reason", "verification failed"))
            attempts.append({"attempt": attempt, "status": "verified"})
            result["vorqyon"] = {"verified": True, "attempts": attempts, "fallback": False}
            _persist(run_id, status="Complete", output=result, verification=verification, attempts=attempt)
            legacy.audit("vorqyon.complete", surface, {"run_id": run_id, "attempts": attempts})
            return result
        except Exception as exc:
            attempts.append({"attempt": attempt, "status": "failed", "error": str(exc)})
            legacy.audit("vorqyon.retry", surface, {"run_id": run_id, "attempt": attempt, "error": str(exc)})
            if attempt < max_attempts:
                await asyncio.sleep(min(0.25 * attempt, 0.75))

    # Explicit zero-cost fallbacks: research retries locally, studio retries native, and
    # orchestration falls back to the standard pipeline. These are real executable paths.
    fallback = None
    if surface == "research":
        fallback = await _run_research(payload | {"max_sources": 3}, run_id)
    elif surface == "studio":
        fallback = await _run_studio(payload | {"provider": "native"}, run_id)
    elif surface == "workflow":
        fallback = await _run_workflow(payload, run_id)
    elif surface == "agent":
        fallback = await _run_agent(payload, run_id)
    if fallback:
        verification = fallback.get("verification") or _verify(fallback.get("result"))
        if verification.get("ok"):
            fallback["vorqyon"] = {"verified": True, "attempts": attempts, "fallback": True}
            _persist(run_id, status="Complete", output=fallback, verification=verification, attempts=max_attempts)
            legacy.audit("vorqyon.fallback_complete", surface, {"run_id": run_id, "attempts": attempts})
            return fallback

    _persist(run_id, status="Failed", output={"error": "Execution failed after retry and fallback"}, attempts=max_attempts)
    legacy.audit("vorqyon.failed", surface, {"run_id": run_id, "attempts": attempts})
    raise HTTPException(502, "Execution failed after retry and fallback")


@router.get("/execution/surfaces")
def execution_surfaces(_: str = Depends(_auth)):
    return {
        "surfaces": sorted(SURFACES),
        "studio_kinds": sorted(STUDIO_KINDS),
        "pipeline": ["Request", "Intent", "Plan", "Execute", "Verify", "Result", "Persist", "Audit"],
        "vorqyon": ["retry", "fallback", "verify", "audit"],
    }


@router.get("/execution/runs")
def execution_runs(limit: int = 50, _: str = Depends(_auth)):
    return legacy.rows("select * from surface_runs order by created desc limit ?", (max(1, min(limit, 200)),))


@router.get("/execution/runs/{run_id}")
def execution_run(run_id: str, _: str = Depends(_auth)):
    item = _row(run_id)
    if not item:
        raise HTTPException(404, "Execution run not found")
    for key in ("input", "output", "verification"):
        if item.get(key):
            item[key] = json.loads(item[key])
    return item


@router.post("/studio/execute")
async def studio_execute(payload: dict, _: str = Depends(_auth)):
    run_id = _new("studio", str(payload.get("kind", "")), payload)
    try:
        result = await _vorqyon("studio", payload, run_id, max(1, min(int(payload.get("max_attempts", 2)), 3)))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _persist(run_id, status="Failed", output={"error": str(exc)})
        raise HTTPException(500, str(exc))


@router.post("/research/execute")
async def research_execute(payload: dict, _: str = Depends(_auth)):
    run_id = _new("research", "research", payload)
    try:
        return await _vorqyon("research", payload, run_id, max(1, min(int(payload.get("max_attempts", 2)), 3)))
    except HTTPException:
        raise
    except Exception as exc:
        _persist(run_id, status="Failed", output={"error": str(exc)})
        raise HTTPException(500, str(exc))


@router.post("/agents/{agent_id}/execute")
async def agent_execute(agent_id: str, payload: dict, _: str = Depends(_auth)):
    payload = dict(payload or {})
    payload["agent_id"] = agent_id
    run_id = _new("agent", agent_id, payload)
    try:
        return await _vorqyon("agent", payload, run_id, max(1, min(int(payload.get("max_attempts", 2)), 3)))
    except HTTPException:
        raise
    except Exception as exc:
        _persist(run_id, status="Failed", output={"error": str(exc)})
        raise HTTPException(500, str(exc))


@router.post("/workflows/{workflow_id}/execute")
async def workflow_execute(workflow_id: str, payload: dict | None = None, _: str = Depends(_auth)):
    data = dict(payload or {})
    data["workflow_id"] = workflow_id
    run_id = _new("workflow", workflow_id, data)
    try:
        return await _vorqyon("workflow", data, run_id, max(1, min(int(data.get("max_attempts", 2)), 3)))
    except HTTPException:
        raise
    except Exception as exc:
        _persist(run_id, status="Failed", output={"error": str(exc)})
        raise HTTPException(500, str(exc))


@router.post("/orchestrator/execute")
async def orchestrator_execute(payload: dict, _: str = Depends(_auth)):
    surface = str(payload.get("surface", "")).strip().lower()
    if surface not in {"studio", "research", "agent", "workflow"}:
        raise HTTPException(400, "surface must be studio, research, agent or workflow")
    run_id = _new("orchestrator", surface, payload)
    try:
        result = await _vorqyon(surface, payload, run_id, max(1, min(int(payload.get("max_attempts", 2)), 3)))
        result["orchestrator"] = {"surface": surface, "unified": True}
        _persist(run_id, status="Complete", output=result, verification=result.get("verification"), attempts=result.get("vorqyon", {}).get("attempts", 1))
        legacy.audit("orchestrator.complete", surface, {"run_id": run_id})
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _persist(run_id, status="Failed", output={"error": str(exc)})
        legacy.audit("orchestrator.failed", surface, {"run_id": run_id, "error": str(exc)})
        raise HTTPException(500, str(exc))


@router.post("/vorqyon/execute-surface")
async def vorqyon_surface(payload: dict, _: str = Depends(_auth)):
    surface = str(payload.get("surface", "")).strip().lower()
    if surface not in {"studio", "research", "agent", "workflow"}:
        raise HTTPException(400, "surface must be studio, research, agent or workflow")
    run_id = _new("vorqyon", surface, payload)
    return await _vorqyon(surface, payload, run_id, max(1, min(int(payload.get("max_attempts", 3)), 3)))


@router.get("/orchestrator/status/{run_id}")
def orchestrator_status(run_id: str, _: str = Depends(_auth)):
    return state_snapshot(run_id) or _row(run_id) or (_ for _ in ()).throw(HTTPException(404, "Execution not found"))
