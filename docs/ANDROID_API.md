# Android API Contract

Base URL: the RAYONE server URL.

1. `POST /api/auth/login` → `{password}` → `{token, expires_in}`
2. Send `Authorization: Bearer <token>` on protected calls.
3. `POST /api/chat` → `{message, project_id?, agent_id?, model_id?}`
4. `GET /api/summary`
5. `GET /api/projects`
6. `GET/POST /api/memory`
7. `GET /api/jobs` and `GET /api/jobs/{id}`
8. `POST /api/tools/execute`
9. `POST /api/workflows/{id}/run`
10. `POST /api/auth/logout`

All protected endpoints return HTTP 401 for missing/expired sessions.
