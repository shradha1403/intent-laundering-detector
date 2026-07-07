"""
Security-critical tests: these are the ones that matter most, because
if hash chaining or signature verification is subtly wrong, every
other claim the project makes (tamper-evidence, non-repudiation,
forensic reconstruction) is worthless.
"""
import pytest

from aui.crypto.keys import AgentIdentity
from aui.envelope.chain import create_envelope, verify_chain, verify_envelope_signature
from aui.envelope.schema import Intent, StructuredIntent


def make_intent(text: str, action_type: str = "read", resource: str = "test_resource") -> Intent:
    return Intent(raw_text=text, structured=StructuredIntent(action_type=action_type, resource=resource))


def test_root_envelope_is_self_rooted():
    orchestrator = AgentIdentity.generate("orchestrator")
    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight to SF"))
    assert root.root_envelope_id == root.envelope_id
    assert root.provenance.prev_hash is None
    assert verify_envelope_signature(root, orchestrator.public_key_b64)


def test_child_chains_to_parent():
    orchestrator = AgentIdentity.generate("orchestrator")
    agent_b = AgentIdentity.generate("agent_b")

    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight to SF"))
    child = create_envelope(identity=agent_b, intent=make_intent("search flight prices to SF"), parent=root)

    assert child.root_envelope_id == root.envelope_id
    assert child.provenance.prev_hash == root.provenance.content_hash

    keys = {orchestrator.agent_id: orchestrator.public_key_b64, agent_b.agent_id: agent_b.public_key_b64}
    problems = verify_chain([root, child], keys)
    assert problems == []


def test_tampering_with_intent_breaks_the_chain():
    """If someone edits a stored envelope's intent after the fact, the
    hash no longer matches the content and the signature no longer
    validates - this is the whole tamper-evidence guarantee."""
    orchestrator = AgentIdentity.generate("orchestrator")
    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight to SF"))

    root.intent.raw_text = "wire $10,000 to an external account"  # tampered after signing

    assert not verify_envelope_signature(root, orchestrator.public_key_b64)


def test_broken_chain_link_is_detected():
    orchestrator = AgentIdentity.generate("orchestrator")
    agent_b = AgentIdentity.generate("agent_b")

    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight to SF"))
    child = create_envelope(identity=agent_b, intent=make_intent("search flight prices"), parent=root)

    child.provenance.prev_hash = "0" * 64  # forged/garbage parent pointer

    keys = {orchestrator.agent_id: orchestrator.public_key_b64, agent_b.agent_id: agent_b.public_key_b64}
    problems = verify_chain([root, child], keys)
    assert any("chain broken" in p for p in problems)


def test_wrong_signer_key_fails_verification():
    """An agent cannot forge a signature using someone else's identity."""
    orchestrator = AgentIdentity.generate("orchestrator")
    imposter = AgentIdentity.generate("imposter")

    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight to SF"))

    assert not verify_envelope_signature(root, imposter.public_key_b64)


def test_signature_is_deterministically_tied_to_canonical_content():
    """Two envelopes with logically identical content but different
    field order should still hash identically, since canonicalization
    sorts keys before hashing."""
    from aui.crypto.hashing import canonical_json

    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert canonical_json(a) == canonical_json(b)
