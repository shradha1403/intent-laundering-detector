"""
Regression test for a Critical finding from a security audit:
BrokerService kept agent private keys in `_identities`, an in-memory
dict, with only the public key persisted. A second BrokerService over
the same DB (which is exactly what happens on a process restart, or
with more than one uvicorn worker behind the FastAPI app) could not
sign anything on behalf of an agent a *different* BrokerService
instance had registered, because it never generated that agent's
keypair itself. Verified directly: `create_envelope` raised
`ValueError: unknown agent ...` in that situation before this fix.

Fix: persist the private key too (see AgentKeyRow in
aui/storage/models.py for the explicit tradeoff that involves), and
have BrokerService lazily reload an identity from the persisted
private key when it isn't already in its own in-memory cache.
"""
from __future__ import annotations

import pytest

from aui.broker.service import BrokerService
from aui.envelope.schema import Intent, StructuredIntent
from aui.storage.db import init_db
from aui.storage.repository import EnvelopeRepository


def make_intent(text, action_type="delegate", resource="test_resource"):
    return Intent(raw_text=text, structured=StructuredIntent(action_type=action_type, resource=resource))


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "restart_test_ledger.db"
    init_db(f"sqlite:///{path}")
    return path


def test_second_broker_instance_can_sign_for_an_agent_it_never_registered(db_path):
    """Simulates a restart: one BrokerService registers an agent and
    creates an envelope, then a brand new BrokerService (fresh,
    empty `_identities`, same underlying DB) creates a follow-up
    envelope delegated by that same agent. This must not raise."""
    first_broker = BrokerService(repo=EnvelopeRepository())
    first_broker.register_agent("orchestrator")
    root = first_broker.create_envelope("orchestrator", make_intent("book me a flight to SF"))

    # A fresh BrokerService, as if the process had restarted. Its
    # `_identities` dict is empty - it never called register_agent for
    # "orchestrator" itself.
    second_broker = BrokerService(repo=EnvelopeRepository())
    assert "orchestrator" not in second_broker._identities

    child = second_broker.create_envelope(
        "orchestrator", make_intent("search flight prices to SF", "read", "flight_booking"), root.envelope_id
    )

    assert child.parent_envelope_id == root.envelope_id
    # The child was actually signed by "orchestrator"'s real key, not
    # some other identity - verify the signature checks out against the
    # public key on file, using the same verification path the chain
    # integrity check uses.
    from aui.crypto.keys import verify_signature

    public_key = second_broker.repo.get_public_key("orchestrator")
    assert verify_signature(public_key, child.provenance.content_hash, child.provenance.signature)


def test_unregistered_agent_still_raises(db_path):
    """Rehydration should only succeed for agents that were actually
    registered (and thus have a persisted private key) at some point.
    An agent nobody ever registered must still fail loudly, not
    silently mint a new identity for them - that would defeat the
    point of requiring registration at all."""
    broker = BrokerService(repo=EnvelopeRepository())
    with pytest.raises(ValueError, match="unknown agent"):
        broker.create_envelope("never_registered", make_intent("do something"))
