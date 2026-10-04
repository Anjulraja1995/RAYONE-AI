from pathlib import Path
import re

HTML = Path("app/rayone.html").read_text(encoding="utf-8")

def test_rayone_has_blueprint_workspaces():
    required = [
        "assistantView","createView","researchView","toolsView","agentsView",
        "workflowsView","automationView","memoryView","filesView","projectsView",
        "mediaView","executionView","controlView","securityView","observabilityView"
    ]
    for item in required:
        assert f'id="{item}"' in HTML

def test_rayone_has_reactive_ai_presence():
    for token in ["class="orb"", "@keyframes breath", "@keyframes think",
                  "@keyframes execute", "@keyframes done", "setExec(", "setState("]:
        assert token in HTML

def test_rayone_wires_real_execution_routes():
    routes = [
        "/api/v2/assistant/chat",
        "/api/v2/tools/catalog",
        "/api/v2/workflows/validate",
        "/api/v2/files",
        "/api/v2/files/upload",
        "/api/v2/automation/schedules",
        "/api/v2/automation/cron",
        "/api/v2/memory/semantic",
        "/api/v2/production/status",
        "/api/v2/production/providers",
        "/api/v2/production/hardening",
        "/api/v2/security/config",
        "/api/v2/providers/health",
        "/api/native/file/"
    ]
    for route in routes:
        assert route in HTML

def test_rayone_is_not_the_old_six_view_shell():
    views = re.findall(r'id="([a-z]+View)" class="view', HTML)
    assert len(views) >= 14
    assert "controlView" in views and "executionView" in views
