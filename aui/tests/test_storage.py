import os

import pytest

from aui.crypto.keys import AgentIdentity
from aui.envelope.chain import create_envelope
from aui.envelope.schema import Intent, StructuredIntent
from aui.storage.db import init_db
from aui.storage.repository import EnvelopeRepository


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    db_path = tmp_path / "test_ledger.db"
    init_db(f"sqlite:///{db_path}")
    return EnvelopeRepository()


def make_intent(text, action_type="read", resource="test_resource"):
    return Intent(raw_text=text, structured=StructuredIntent(action_type=action_type, resource=resource))


def test_save_and_retrieve_envelope(repo):
    orchestrator = AgentIdentity.generate("orchestrator")
    repo.register_key(orchestrator.agent_id, orchestrator.public_key_b64)

    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight to SF"))
    repo.save_envelope(root)

    fetched = repo.get_envelope(root.envelope_id)
    assert fetched is not None
    assert fetched.intent.raw_text == "book a flight to SF"
    assert fetched.provenance.content_hash == root.provenance.content_hash


def test_chain_walk_returns_root_to_leaf_order(repo):
    orchestrator = AgentIdentity.generate("orchestrator")
    agent_b = AgentIdentity.generate("agent_b")
    agent_c = AgentIdentity.generate("agent_c")

    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight to SF"))
    repo.save_envelope(root)

    mid = create_envelope(identity=agent_b, intent=make_intent("search flight prices"), parent=root)
    repo.save_envelope(mid)

    leaf = create_envelope(identity=agent_c, intent=make_intent("charge the card for the ticket"), parent=mid)
    repo.save_envelope(leaf)

    chain = repo.get_chain(leaf.envelope_id)
    assert [e.envelope_id for e in chain] == [root.envelope_id, mid.envelope_id, leaf.envelope_id]


def test_get_root_from_any_descendant(repo):
    orchestrator = AgentIdentity.generate("orchestrator")
    agent_b = AgentIdentity.generate("agent_b")

    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight to SF"))
    repo.save_envelope(root)
    child = create_envelope(identity=agent_b, intent=make_intent("search flights"), parent=root)
    repo.save_envelope(child)

    found_root = repo.get_root(child.envelope_id)
    assert found_root.envelope_id == root.envelope_id
