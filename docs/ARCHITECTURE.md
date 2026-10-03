# RAYONE Architecture v2

## Control plane
FastAPI is the single control plane. SQLite is the portable persistence layer.

## Execution
1. Authenticate session.
2. Load project/model/provider context.
3. Route obvious local intents to built-in tools.
4. Route configured AI requests to an enabled provider/model.
5. Fall back to local-first deterministic operation when no provider is available.
6. Emit events and audit records.

## Extensibility
Tools, models, providers, agents and workflows are persisted as records. Built-in tools are deterministic and safe. HTTP tools can be registered with an explicit URL.

## Recovery
Checkpoints snapshot operational configuration and data; restore replaces those tables from a selected snapshot. Export/import provides portable state transfer.

## Background execution
The built-in async worker consumes queued jobs for chat, tools and workflows.

## Voice
The browser uses Web Speech APIs for speech recognition and speech synthesis where the user's browser supports them. No paid voice API is required.
