"""
Building and verifying hash-linked chains of envelopes.

This is the tamper-evidence layer: if anyone edits a past envelope,
its content_hash changes, which breaks every prev_hash pointing
forward from it, which breaks every signature built on top of that,
so the break is detectable no matter where in the chain it happens.
"""
from __future__ import annotations

from typing import Optional

from aui.crypto.hashing import content_hash
from aui.crypto.keys import AgentIdentity, verify_signature
from aui.envelope.schema import Envelope, Intent, Provenance


def create_envelope(
    *,
    identity: AgentIdentity,
    intent: Intent,
    parent: Optional[Envelope] = None,
) -> Envelope:
    """Create and sign a new envelope.

    If `parent` is None this is a root envelope (root_envelope_id ==
    its own envelope_id, prev_hash == None). Otherwise it inherits
    root_envelope_id from the parent and chains prev_hash to the
    parent's content_hash.
    """
    # Build a provisional envelope to get a stable envelope_id before hashing.
    provisional = Envelope(
        parent_envelope_id=parent.envelope_id if parent else None,
        root_envelope_id=parent.root_envelope_id if parent else "PENDING",
        agent_id=identity.agent_id,
        intent=intent,
        provenance=Provenance(
            prev_hash=parent.provenance.content_hash if parent else None,
            content_hash="PENDING",
            signature="PENDING",
            signer_pubkey_id=identity.agent_id,
        ),
    )
    if not parent:
        provisional.root_envelope_id = provisional.envelope_id

    chash = content_hash(provisional.canonical_payload())
    signature = identity.sign(chash)

    provisional.provenance.content_hash = chash
    provisional.provenance.signature = signature
    return provisional


def verify_envelope_signature(envelope: Envelope, public_key_b64: str) -> bool:
    """Check that content_hash matches the payload and the signature is valid."""
    expected_hash = content_hash(envelope.canonical_payload())
    if expected_hash != envelope.provenance.content_hash:
        return False
    return verify_signature(public_key_b64, envelope.provenance.content_hash, envelope.provenance.signature)


def verify_chain(envelopes: list[Envelope], public_keys: dict[str, str]) -> list[str]:
    """Verify an ordered root-to-leaf chain of envelopes.

    `public_keys` maps agent_id -> public_key_b64.
    Returns a list of problem descriptions; empty list means the
    chain is fully intact (every hash link and every signature
    checks out).
    """
    problems: list[str] = []
    prev: Optional[Envelope] = None

    for env in envelopes:
        pubkey = public_keys.get(env.agent_id)
        if pubkey is None:
            problems.append(f"{env.envelope_id}: no known public key for agent {env.agent_id}")
            continue

        if not verify_envelope_signature(env, pubkey):
            problems.append(f"{env.envelope_id}: signature or content hash invalid")

        if prev is None:
            if env.provenance.prev_hash is not None:
                problems.append(f"{env.envelope_id}: expected root (prev_hash=None) but has a parent hash")
        else:
            if env.provenance.prev_hash != prev.provenance.content_hash:
                problems.append(
                    f"{env.envelope_id}: prev_hash does not match parent's content_hash "
                    f"(chain broken between {prev.envelope_id} and {env.envelope_id})"
                )
        prev = env

    return problems
