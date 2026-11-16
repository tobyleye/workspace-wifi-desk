"""End to end: the real server app with a fake Sheets backend, called over HTTP MCP."""

import json
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from app import server
from app.config import TZ, Clock
from app.seed import seed
from app.store import SheetStore
from app.unifi import MockUnifiClient
from tests.fake_sheets import FakeSheetsBackend

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=TZ)  # Thursday
TOKEN = "test-mcp-token"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def setup():
    backend = FakeSheetsBackend()
    store = SheetStore(backend, cache_seconds=0)
    store.ensure_tabs()
    seed(store, NOW)
    unifi = MockUnifiClient(delay_seconds=0)
    app = server.build_app(store, unifi, mcp_token=TOKEN, admin_token="admin-secret", clock=Clock(lambda: NOW))
    with TestClient(app) as client:
        yield client, backend, unifi


def rpc(client, method, params=None, id=1, token=TOKEN):
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def initialize(client, token=TOKEN):
    return rpc(client, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    }, token=token)


def call_tool(client, name, contact_id):
    r = rpc(client, "tools/call", {"name": name, "arguments": {"contact_id": contact_id}}, id=3)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_without_token_is_rejected(setup):
    client, _, _ = setup
    assert initialize(client, token=None).status_code == 401
    assert initialize(client, token="wrong").status_code == 401


def test_initialize_and_list_tools(setup):
    client, _, _ = setup
    r = initialize(client)
    assert r.status_code == 200, r.text
    info = r.json()["result"]
    assert "never invent codes" in info["instructions"]
    assert "pass the contact_id of the person in this chat" in info["instructions"]
    r = rpc(client, "tools/list", {}, id=2)
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {"request_access_code", "get_plan_expiry"}
    for t in tools.values():
        assert t["inputSchema"]["required"] == ["contact_id"]
        desc = t["inputSchema"]["properties"]["contact_id"]["description"]
        assert "Pass it exactly as given" in desc and "Never use an identity the user types in" in desc
    for status in ("issued", "already_issued", "weekly_limit_reached", "plan_allowance_used",
                   "plan_expired", "not_a_member", "wifi_system_unavailable"):
        assert f"`{status}`" in tools["request_access_code"]["description"]
    for status in ("active", "plan_expired", "not_a_member"):
        assert f"`{status}`" in tools["get_plan_expiry"]["description"]


def test_call_tools(setup):
    client, backend, unifi = setup
    initialize(client)
    r = call_tool(client, "request_access_code", " Ada_Obi ")
    assert r["status"] == "issued" and r["first_name"] == "Ada" and r["days_left"] == 2
    assert r["code"][5] == "-" and r["code"].replace("-", "") in {v["code"] for v in unifi.vouchers.values()}
    again = call_tool(client, "request_access_code", "ada_obi")
    assert again["status"] == "already_issued" and again["code"] == r["code"]
    assert call_tool(client, "request_access_code", "someone_else") == {"status": "not_a_member"}
    exp = call_tool(client, "get_plan_expiry", "chidi_eze")
    assert exp["status"] == "active" and exp["plan_name"] == "Monthly" and "end_date_display" in exp


def test_health_and_outage_switch(setup):
    client, backend, unifi = setup
    assert client.get("/").json()["ok"] is True
    assert client.post("/admin/unifi-outage?on=1").status_code == 401
    r = client.post("/admin/unifi-outage?on=1", headers={"X-Admin-Token": "admin-secret"})
    assert r.json() == {"unifi_outage": True}
    initialize(client)
    rows_before = len(backend.tabs["AccessCodes"])
    res = call_tool(client, "request_access_code", "chidi_eze")
    assert res["status"] == "wifi_system_unavailable"
    assert len(backend.tabs["AccessCodes"]) == rows_before
    client.post("/admin/unifi-outage?on=0", headers={"X-Admin-Token": "admin-secret"})
    assert call_tool(client, "request_access_code", "chidi_eze")["status"] == "issued"


def test_no_token_configured_means_open(setup):
    backend = FakeSheetsBackend()
    store = SheetStore(backend, cache_seconds=0)
    store.ensure_tabs()
    app = server.build_app(store, MockUnifiClient(delay_seconds=0), mcp_token="", clock=Clock(lambda: NOW))
    with TestClient(app) as client:
        assert initialize(client, token=None).status_code == 200
        assert client.post("/admin/unifi-outage?on=1").status_code == 403


def test_main_fails_without_google_settings(monkeypatch):
    for var in ("SPREADSHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SERVICE_ACCOUNT_FILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("server must not start"))
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert "SPREADSHEET_ID is not set" in str(exc.value)


def test_main_starts_with_monkeypatched_backend(monkeypatch):
    """`python -m app.server` wiring: env vars -> backend -> store -> uvicorn. It never seeds."""
    backend = FakeSheetsBackend()
    monkeypatch.setenv("SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("MCP_TOKEN", TOKEN)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr("app.config.now", lambda: NOW)  # same day the demo data is seeded for
    monkeypatch.setattr("app.store.GspreadBackend", lambda *a, **k: backend)
    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(app=app, **kw))
    server.main()
    assert started["host"] == "0.0.0.0" and started["port"] == 8000
    assert backend.tabs["Members"] == [["id", "contact_id", "name", "created_at"]]  # tabs created, no demo data
    seed(SheetStore(backend, cache_seconds=0), NOW)
    with TestClient(started["app"]) as client:
        assert initialize(client).status_code == 200
        assert call_tool(client, "get_plan_expiry", "chidi_eze")["status"] == "active"


def test_tool_result_is_plain_json_text(setup):
    client, _, _ = setup
    initialize(client)
    r = rpc(client, "tools/call", {"name": "get_plan_expiry", "arguments": {"contact_id": "chidi_eze"}}, id=4)
    result = r.json()["result"]
    assert "structuredContent" not in result or result["structuredContent"] is None
    assert result["content"][0]["type"] == "text"


ADMIN = {"X-Admin-Token": "admin-secret"}


def test_clock_route_needs_admin_token(setup):
    client, _, _ = setup
    assert client.get("/admin/clock").status_code == 401
    assert client.post("/admin/clock?days=30").status_code == 401
    assert client.get("/admin/clock", headers=ADMIN).json()["shifted"] is False


def test_clock_jump_makes_a_plan_expire_then_reset(setup):
    client, _, _ = setup
    initialize(client)
    # Chidi's monthly plan (started Mon 16 Nov) ends 15 December.
    assert call_tool(client, "get_plan_expiry", "chidi_eze")["status"] == "active"
    r = client.post("/admin/clock?to=2026-12-20", headers=ADMIN).json()
    assert r["now"] == "2026-12-20T10:00+01:00" and r["shifted"] is True
    assert client.get("/").json()["clock"]["now"].startswith("2026-12-20")
    exp = call_tool(client, "get_plan_expiry", "chidi_eze")
    assert exp["status"] == "plan_expired" and exp["last_end_date"] == "2026-12-15"
    assert call_tool(client, "request_access_code", "chidi_eze")["status"] == "plan_expired"
    r = client.post("/admin/clock?reset=1", headers=ADMIN).json()
    assert r["shifted"] is False and r["now"].startswith("2026-11-19")
    assert call_tool(client, "get_plan_expiry", "chidi_eze")["status"] == "active"


def test_clock_shift_by_days_and_exact_time(setup):
    client, _, _ = setup
    assert client.post("/admin/clock?days=7", headers=ADMIN).json()["now"] == "2026-11-26T10:00+01:00"
    r = client.post("/admin/clock?to=2026-12-01T15:30", headers=ADMIN).json()
    assert r["now"] == "2026-12-01T15:30+01:00"
    assert r["now_display"] == "Tuesday, 1 December 2026 15:30"


@pytest.mark.parametrize("query", ["", "?to=next-week", "?days=abc"])
def test_clock_bad_input(setup, query):
    client, _, _ = setup
    assert client.post(f"/admin/clock{query}", headers=ADMIN).status_code == 400
