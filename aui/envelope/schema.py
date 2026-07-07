"""
The Intent Envelope: the core data structure of the whole project.

Every delegation hop in a multi-agent system creates one of these.
It carries three things forward: what this agent claims it's doing
(intent), what it actually did (action_receipt, filled in after
execution), and the cryptographic link back to its parent
(provenance), so the whole chain can be replayed and verified later.

fidelity is intentionally NOT trusted input - it's always computed
server-side by the Fidelity Engine and attached after the fact. An
agent cannot claim its own fidelity score.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class StructuredIntent(BaseModel):
    """The machine-checkable part of an intent.

    action_type / resource / constraints exist specifically so that
    the action-grounding check (stage 2 of the fidelity engine) has
    something deterministic to check the real tool call against,
    instead of relying on an LLM to judge everything.
    """

    action_type: str = Field(..., description="e.g. read, write, purchase, external_call, delegate")
    resource: str = Field(..., description="e.g. payment_api, calendar, email, flight_booking")
    constraints: list[str] = Field(default_factory=list, description='e.g. "max_amount:50usd", "scope:read_only"')


class Intent(BaseModel):
    raw_text: str = Field(..., description="Free-text restatement of the sub-intent, in the agent's own words")
    structured: StructuredIntent


class ActionReceipt(BaseModel):
    """What actually happened, captured by the Action Interceptor.

    This is populated after the envelope is created and the agent
    goes on to actually call a tool. It is the thing that lets us
    catch an agent that stated an honest intent but then did
    something else - the gap that pure intent-to-intent comparison
    would miss entirely.
    """

    tool_name: str
    arguments: dict = Field(default_factory=dict)
    result_summary: str = ""
    executed_at: str = Field(default_factory=_now)


class Provenance(BaseModel):
    prev_hash: Optional[str] = Field(None, description="content_hash of the parent envelope, null for root")
    content_hash: str = Field(..., description="sha256 of this envelope's canonical content, excluding signature")
    signature: str = Field(..., description="base64 Ed25519 signature over content_hash")
    signer_pubkey_id: str = Field(..., description="agent_id whose registered public key signed this")


class FidelityResult(BaseModel):
    pairwise_score: Optional[float] = None
    action_grounding_score: Optional[float] = None
    transitive_score: Optional[float] = None
    flags: list[str] = Field(default_factory=list)


class Envelope(BaseModel):
    envelope_id: str = Field(default_factory=_new_id)
    parent_envelope_id: Optional[str] = None
    root_envelope_id: str
    agent_id: str
    created_at: str = Field(default_factory=_now)

    intent: Intent
    action_receipt: Optional[ActionReceipt] = None
    provenance: Provenance
    fidelity: FidelityResult = Field(default_factory=FidelityResult)

    def canonical_payload(self) -> dict:
        """The subset of fields that get hashed and signed.

        Deliberately excludes `provenance.signature` (can't sign over
        your own signature) and `fidelity` (computed after the fact
        by the Broker, not part of what the agent asserts).

        Also deliberately excludes `action_receipt`. This matters:
        the agent signs its envelope at the moment it DECLARES a
        sub-intent, before it has acted. The action_receipt only
        exists later, once the interceptor has recorded what really
        happened. If action_receipt were part of the signed payload,
        attaching it after the fact would invalidate the original
        signature every time (the hash would no longer match what
        was signed), since the content changed after signing. Keeping
        the two separate is also the more honest design: the
        signature is a fixed, non-repudiable claim of intent at time
        T; the receipt is an independently-captured fact recorded
        after T. Action-grounding compares the two precisely because
        they come from different moments and different sources of
        truth, not because they're both folded into one signature.
        """
        return {
            "envelope_id": self.envelope_id,
            "parent_envelope_id": self.parent_envelope_id,
            "root_envelope_id": self.root_envelope_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "intent": self.intent.model_dump(),
            "prev_hash": self.provenance.prev_hash,
        }
