# RAYONE AI v2.0 — Universal AI Command Center

RAYONE is a free/local-first, portable AI assistant core with a single FastAPI control plane, SQLite persistence, encrypted secrets, provider/model routing, tools, agents, workflows, memory, background jobs, checkpoints/restore, audit/events, import/export and a responsive web dashboard.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000`.

Default development password: `RAYONE-Admin-2026`. **Change it in production with `RAYONE_ADMIN_PASSWORD`.**

## Core modules

- Authentication/session management
- Projects
- Providers: OpenAI-compatible, Ollama, LM Studio compatible endpoints
- Models
- Built-in tools: echo, calculator, datetime, memory search, JSON
- Extensible HTTP tools
- Agents
- Workflows and workflow execution
- Memory
- Background job worker
- Events and audit trail
- Encrypted secrets using Fernet
- Checkpoint creation and restore
- Full state export/import
- Connectivity diagnostics
- Browser voice input/output
- Responsive command-center UI

## Architecture

Browser → FastAPI → Core services → SQLite

AI requests route through enabled model/provider records. If no provider is available, RAYONE remains usable in local-first mode with deterministic responses and built-in tools. Workflows can chain tool, chat and memory steps. Jobs execute asynchronously through the built-in worker.

## Security

Provider API keys and secrets are encrypted at rest. Keep `RAYONE_SECRET_KEY` private and persistent; changing it makes previously encrypted secrets unreadable. Sessions are bearer tokens stored server-side with 24-hour expiry.

For internet exposure, put RAYONE behind HTTPS and a trusted reverse proxy, use a strong admin password and a unique Fernet key, and restrict network access.

## Android

The REST API is mobile-client friendly. An Android client can authenticate at `/api/auth/login` and consume the same `/api/*` endpoints. This release does not bundle a native Android application.


## RAYONE v2 control plane

The current release also exposes a versioned universal control plane under `/api/v2/*` and a replacement command-center dashboard.

### Execution architecture

```
User → Auth → RAYONE Core → Context/Memory → Intent/Planner → Router
     → Model / Tool / Agent / Workflow / Research / Media
     → VORQYON verification → Result → Trace / Audit / Metrics
     → Checkpoint / Recovery
```

The v2 layer provides:
- explicit RAYONE state traces and execution IDs
- permission records and consequential-action approval queue
- assistant streaming endpoint
- agent execution loop with bounded steps
- semantic-lite memory ranking plus file indexing
- TXT/MD/CSV/JSON/HTML/XML, PDF, DOCX and XLSX extraction
- scheduled interval automation
- workspace file registry
- media job contract for image/video/audio/voice/music adapters
- connector registry
- web research/search adapter
- optional GitHub repository adapter using `GITHUB_TOKEN`
- local browser fetch/text extraction, OCR adapter and media metadata probe
- diagnostics, metrics and observability endpoints
- responsive universal command-center dashboard

External services are intentionally adapter-based: RAYONE does not fabricate a provider or connector that has not been configured. Credentials remain server-side.

## Zero-cost/local-first rule

The core platform does not require a paid AI provider. It can run locally with deterministic tools and local fallback behavior. No paid API, subscription or credit is a mandatory dependency.

RAYONE now ships an executable built-in local tool pack covering 38 functional families including text, math, JSON, encoding, cryptographic hashing, regex, date/time, lists, planning, finance, geometry, security, automation, web, translation, document and media operations. These tools are registered in the same tool registry and run through the same execution engine as external tools.

External AI, media, messaging or cloud connectors are optional authorization/integration layers. They must never be represented as active until credentials or a local runtime actually makes them executable.


## Completion-layer capabilities

The control plane also includes:
- job cancellation and retry
- provider health checks and ordered failover
- interval, cron and one-time scheduling
- workspace file download/re-index and size statistics
- backup creation/list/download/delete
- security session inspection/revocation and persisted password override
- generic credential-gated connector execution with approval for mutations
- GitLab read/control adapter with approval-gated mutations
- capability reporting
- browser speech recognition plus speech synthesis in the web dashboard
- a zero-cost Android REST client shell under `android/`
- an optional Electron desktop shell under `desktop/`

### Native/External adapter boundary

RAYONE exposes real adapter contracts for capabilities that require an external runtime, credential, model, browser binary or media engine. It does not pretend those services are active when they are not configured. This keeps the local-first core functional while allowing production adapters to be attached without changing the control plane.


## Universal execution rule

A capability is considered implemented only when its UI/control surface, backend contract, execution path, error handling and regression test exist. A connector registration screen alone is not counted as a completed integration.

The workflow module includes a live draft builder, validation endpoint and executable saved workflows. The local tool catalog is available at `/api/v2/tools/catalog` and execution at `/api/v2/tools/run`.
