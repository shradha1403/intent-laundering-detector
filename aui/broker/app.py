"""
FastAPI wrapper around BrokerService. Thin on purpose - request
validation and HTTP routing only, all real logic lives in
BrokerService so it's testable without a running server.

Run with:  uvicorn aui.broker.app:app --reload
Docs at:   http://localhost:8000/docs

Auth: a security audit found every endpoint here was unauthenticated -
anyone who could reach the port could register agents or write to the
audit ledger, which directly contradicts the point of a system meant
to detect authority misuse. Every route now requires
`Authorization: Bearer <token>`, checked against AUI_API_KEY. This is
a shared-secret gate on API ACCESS, not agent attestation - it does
not, and cannot by itself, fix the separate documented issue that
agent identity is self-registered (see aui/crypto/keys.py). Someone
holding the shared token can still register an agent under any name.
Real per-agent attestation needs hardware-backed keys or mTLS client
certs, which is genuinely out of scope for an MVP running on a laptop -
see docs/ROADMAP.md.
"""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from aui.envelope.schema import Intent, StructuredIntent
from aui.broker.service import BrokerService
from aui.storage.db import init_db
from aui.forensics.report import build_forensic_report


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # init_db() used to run at MODULE IMPORT time with a hardcoded,
    # cwd-relative default ("sqlite:///aui_ledger.db"), which a security
    # audit flagged two ways: (1) merely importing this module (e.g. to
    # build a TestClient in a test) had the side effect of creating a
    # database file wherever the test happened to run from, and (2)
    # there was no way to point the API at a specific ledger file
    # without editing this source file. AUI_DB_URL makes the location
    # explicit and configurable; running init_db() from a lifespan
    # handler instead of at import time means importing this module no
    # longer touches the filesystem at all, which is what let this file
    # finally get a real test (see aui/tests/test_broker_api.py)
    # instead of the audit's "no FastAPI test exists" finding.
    init_db(os.environ.get("AUI_DB_URL", "sqlite:///aui_ledger.db"))
    yield


app = FastAPI(
    title="Authority-Use Integrity - Intent Broker",
    description="Detects intent laundering across delegated multi-agent tasks.",
    version="0.1.0",
    lifespan=_lifespan,
)

broker = BrokerService()

API_KEY = os.environ.get("AUI_API_KEY")
if not API_KEY:
    # No token configured: generate a one-time token for this process
    # instead of running with no auth at all, same idea as Jupyter's
    # default token model. Printed once at startup so a local demo
    # still works without extra setup, but nobody can hit this API
    # from outside without having seen this process's own stdout.
    API_KEY = secrets.token_urlsafe(32)
    print(f"[aui] AUI_API_KEY not set - generated a one-time token for this process:\n[aui]   {API_KEY}")
    print("[aui] pass it as: Authorization: Bearer <token>")
    print("[aui] set AUI_API_KEY yourself for anything beyond a laptop demo (it resets every restart otherwise)")


def require_api_key(authorization: Optional[str] = Header(default=None)) -> None:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, API_KEY):
        raise HTTPException(status_code=401, detail="invalid bearer token")


class RegisterAgentRequest(BaseModel):
    agent_id: str


class StructuredIntentIn(BaseModel):
    action_type: str
    resource: str
    constraints: list[str] = []


class CreateEnvelopeRequest(BaseModel):
    agent_id: str
    raw_text: str
    structured: StructuredIntentIn
    parent_envelope_id: Optional[str] = None


class ActionRequest(BaseModel):
    tool_name: str
    arguments: dict = {}


@app.post("/agents/register", dependencies=[Depends(require_api_key)])
def register_agent(req: RegisterAgentRequest):
    identity = broker.register_agent(req.agent_id)
    return {"agent_id": req.agent_id, "public_key_b64": identity.public_key_b64}


@app.post("/envelopes", dependencies=[Depends(require_api_key)])
def create_envelope(req: CreateEnvelopeRequest):
    try:
        intent = Intent(raw_text=req.raw_text, structured=StructuredIntent(**req.structured.model_dump()))
        envelope = broker.create_envelope(req.agent_id, intent, req.parent_envelope_id)
        return envelope.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/envelopes/{envelope_id}/actions", dependencies=[Depends(require_api_key)])
def register_action(envelope_id: str, req: ActionRequest):
    try:
        envelope = broker.execute_action(envelope_id, req.tool_name, req.arguments)
        return envelope.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/envelopes/{envelope_id}/chain", dependencies=[Depends(require_api_key)])
def get_chain(envelope_id: str):
    chain = broker.get_chain(envelope_id)
    if not chain:
        raise HTTPException(status_code=404, detail="envelope not found")
    return [e.model_dump() for e in chain]


@app.post("/envelopes/{envelope_id}/verify/transitive", dependencies=[Depends(require_api_key)])
def verify_transitive(envelope_id: str):
    try:
        score, flags = broker.verify_transitive(envelope_id)
        return {"transitive_score": score, "flags": flags}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/envelopes/{envelope_id}/verify/integrity", dependencies=[Depends(require_api_key)])
def verify_integrity(envelope_id: str):
    problems = broker.verify_chain_integrity(envelope_id)
    return {"tamper_evident_problems": problems, "intact": problems == []}


@app.get("/forensics/{envelope_id}/report", dependencies=[Depends(require_api_key)])
def forensic_report(envelope_id: str):
    chain = broker.get_chain(envelope_id)
    if not chain:
        raise HTTPException(status_code=404, detail="envelope not found")
    integrity_problems = broker.verify_chain_integrity(envelope_id)
    return build_forensic_report(chain, integrity_problems, broker.repo)
