"""
FastAPI wrapper around BrokerService. Thin on purpose - request
validation and HTTP routing only, all real logic lives in
BrokerService so it's testable without a running server.

Run with:  uvicorn aui.broker.app:app --reload
Docs at:   http://localhost:8000/docs
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from aui.envelope.schema import Intent, StructuredIntent
from aui.broker.service import BrokerService
from aui.storage.db import init_db
from aui.forensics.report import build_forensic_report

app = FastAPI(
    title="Authority-Use Integrity - Intent Broker",
    description="Detects intent laundering across delegated multi-agent tasks.",
    version="0.1.0",
)

init_db()
broker = BrokerService()


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


@app.post("/agents/register")
def register_agent(req: RegisterAgentRequest):
    identity = broker.register_agent(req.agent_id)
    return {"agent_id": req.agent_id, "public_key_b64": identity.public_key_b64}


@app.post("/envelopes")
def create_envelope(req: CreateEnvelopeRequest):
    try:
        intent = Intent(raw_text=req.raw_text, structured=StructuredIntent(**req.structured.model_dump()))
        envelope = broker.create_envelope(req.agent_id, intent, req.parent_envelope_id)
        return envelope.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/envelopes/{envelope_id}/actions")
def register_action(envelope_id: str, req: ActionRequest):
    try:
        envelope = broker.execute_action(envelope_id, req.tool_name, req.arguments)
        return envelope.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/envelopes/{envelope_id}/chain")
def get_chain(envelope_id: str):
    chain = broker.get_chain(envelope_id)
    if not chain:
        raise HTTPException(status_code=404, detail="envelope not found")
    return [e.model_dump() for e in chain]


@app.post("/envelopes/{envelope_id}/verify/transitive")
def verify_transitive(envelope_id: str):
    try:
        score, flags = broker.verify_transitive(envelope_id)
        return {"transitive_score": score, "flags": flags}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/envelopes/{envelope_id}/verify/integrity")
def verify_integrity(envelope_id: str):
    problems = broker.verify_chain_integrity(envelope_id)
    return {"tamper_evident_problems": problems, "intact": problems == []}


@app.get("/forensics/{envelope_id}/report")
def forensic_report(envelope_id: str):
    chain = broker.get_chain(envelope_id)
    if not chain:
        raise HTTPException(status_code=404, detail="envelope not found")
    integrity_problems = broker.verify_chain_integrity(envelope_id)
    return build_forensic_report(chain, integrity_problems, broker.repo)
