"""
The FastAPI layer (aui/broker/app.py) had no test at all before a
security audit pointed it out - the underlying BrokerService was well
tested, but nothing exercised the actual HTTP routes, so nothing would
have caught either of the two Critical findings that were specific to
the API layer: every endpoint was unauthenticated, and importing the
module had the side effect of writing a database file to whatever the
current working directory happened to be.

Each test reloads aui.broker.app after setting env vars, rather than
importing it once at module scope, because AUI_API_KEY and AUI_DB_URL
are both read at import/startup time - reloading is what lets each
test control its own token and its own throwaway database file
instead of every test in this file fighting over one shared module
state.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _fresh_app(monkeypatch, tmp_path, token: str = "test-secret-token"):
    db_path = tmp_path / f"api_test_{id(monkeypatch)}.db"
    monkeypatch.setenv("AUI_API_KEY", token)
    monkeypatch.setenv("AUI_DB_URL", f"sqlite:///{db_path}")
    from aui.broker import app as app_module

    importlib.reload(app_module)
    return app_module


def test_request_without_token_is_rejected(monkeypatch, tmp_path):
    app_module = _fresh_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        resp = client.post("/agents/register", json={"agent_id": "agent_a"})
        assert resp.status_code == 401


def test_request_with_wrong_token_is_rejected(monkeypatch, tmp_path):
    app_module = _fresh_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        resp = client.post(
            "/agents/register",
            json={"agent_id": "agent_a"},
            headers={"Authorization": "Bearer not-the-right-token"},
        )
        assert resp.status_code == 401


def test_request_with_correct_token_succeeds(monkeypatch, tmp_path):
    app_module = _fresh_app(monkeypatch, tmp_path, token="correct-token")
    with TestClient(app_module.app) as client:
        resp = client.post(
            "/agents/register",
            json={"agent_id": "agent_a"},
            headers={"Authorization": "Bearer correct-token"},
        )
        assert resp.status_code == 200
        assert "public_key_b64" in resp.json()


def test_full_envelope_flow_through_the_real_http_layer(monkeypatch, tmp_path):
    """Not just auth in isolation - register an agent, create an
    envelope, attach an action, and pull the forensic report, all
    through actual HTTP requests, to prove the routes still work end
    to end with auth wired in and the DB now coming from AUI_DB_URL
    instead of a hardcoded cwd-relative path."""
    app_module = _fresh_app(monkeypatch, tmp_path, token="flow-token")
    headers = {"Authorization": "Bearer flow-token"}
    with TestClient(app_module.app) as client:
        reg = client.post("/agents/register", json={"agent_id": "orchestrator"}, headers=headers)
        assert reg.status_code == 200

        env = client.post(
            "/envelopes",
            json={
                "agent_id": "orchestrator",
                "raw_text": "book me a flight to SF",
                "structured": {"action_type": "delegate", "resource": "flight_booking", "constraints": []},
            },
            headers=headers,
        )
        assert env.status_code == 200
        envelope_id = env.json()["envelope_id"]

        action = client.post(
            f"/envelopes/{envelope_id}/actions",
            json={"tool_name": "search_flights", "arguments": {"destination": "SF"}},
            headers=headers,
        )
        assert action.status_code == 200

        report = client.get(f"/forensics/{envelope_id}/report", headers=headers)
        assert report.status_code == 200
        assert "any_hop_flagged" in report.json()


def test_db_url_is_configurable_via_env_var(monkeypatch, tmp_path):
    """AUI_DB_URL should control exactly where the ledger lives -
    previously this was hardcoded, so there was no way to point the
    API at a specific file without editing source."""
    db_path = tmp_path / "custom_location.db"
    monkeypatch.setenv("AUI_API_KEY", "token")
    monkeypatch.setenv("AUI_DB_URL", f"sqlite:///{db_path}")
    from aui.broker import app as app_module

    importlib.reload(app_module)
    with TestClient(app_module.app):
        pass
    assert db_path.exists(), "expected the ledger to be created at the AUI_DB_URL location, not a hardcoded default"
