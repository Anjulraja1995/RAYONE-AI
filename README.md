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
