"""
A security audit flagged concurrent access as "Cannot verify" - nothing
in the suite exercised more than one thread touching the ledger at
once, despite aui/storage/db.py explicitly passing
`check_same_thread=False` to SQLite, which only makes sense if
concurrent access is actually expected to happen (e.g. FastAPI serving
more than one request at a time).

This doesn't fix a concurrency bug - it establishes, with an actual
test instead of a guess, what currently happens under concurrent
writes: whether envelopes survive being created from multiple threads
against the same underlying SQLite file without cross-contamination or
silent data loss. SQLite itself only allows one writer at a time
regardless of what any Python-level locking does, so some amount of
serialization (and, under heavier contention than this test applies,
`database is locked` errors) is expected and honestly documented here,
not hidden.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from aui.crypto.keys import AgentIdentity
from aui.envelope.chain import create_envelope
from aui.envelope.schema import Intent, StructuredIntent
from aui.storage.db import init_db
from aui.storage.repository import EnvelopeRepository


def make_intent(text, agent_id):
    return Intent(
        raw_text=f"{text} (from {agent_id})",
        structured=StructuredIntent(action_type="read", resource="test_resource"),
    )


@pytest.fixture()
def repo(tmp_path):
    db_path = tmp_path / "concurrency_test_ledger.db"
    init_db(f"sqlite:///{db_path}")
    return EnvelopeRepository()


def _register_and_save(repo: EnvelopeRepository, agent_id: str):
    identity = AgentIdentity.generate(agent_id)
    repo.register_key(identity.agent_id, identity.public_key_b64)
    envelope = create_envelope(identity=identity, intent=make_intent("do something", agent_id))
    repo.save_envelope(envelope)
    return envelope


def test_concurrent_envelope_creation_does_not_corrupt_or_cross_contaminate(repo):
    """N threads each register a distinct agent and save one envelope,
    all against the same repo/DB at once. Every envelope must come back
    exactly as it was written, under the agent that actually wrote it,
    with no exceptions escaping and no result silently dropped."""
    n = 8
    agent_ids = [f"concurrent_agent_{i}" for i in range(n)]

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = {pool.submit(_register_and_save, repo, aid): aid for aid in agent_ids}
        results = {}
        errors = []
        for future in as_completed(futures):
            agent_id = futures[future]
            try:
                results[agent_id] = future.result()
            except Exception as e:  # noqa: BLE001 - deliberately capturing anything for the assertion below
                errors.append((agent_id, e))

    assert not errors, f"concurrent envelope creation raised: {errors}"
    assert len(results) == n

    for agent_id, envelope in results.items():
        fetched = repo.get_envelope(envelope.envelope_id)
        assert fetched is not None, f"envelope for {agent_id} went missing under concurrent writes"
        assert fetched.agent_id == agent_id
        assert fetched.intent.raw_text == envelope.intent.raw_text
        assert fetched.provenance.content_hash == envelope.provenance.content_hash


def test_concurrent_reads_of_the_same_chain_are_consistent(repo):
    """Once a chain is written, many threads reading it back at once
    should all see the same, correct chain - read concurrency is a much
    weaker claim than write concurrency, but it's the access pattern
    the dashboard and the FastAPI GET routes actually rely on, so it's
    worth its own explicit check rather than assuming reads are fine
    because writes were."""
    orchestrator = AgentIdentity.generate("orchestrator")
    repo.register_key(orchestrator.agent_id, orchestrator.public_key_b64)
    root = create_envelope(identity=orchestrator, intent=make_intent("book a flight", "orchestrator"))
    repo.save_envelope(root)

    agent_b = AgentIdentity.generate("agent_b")
    repo.register_key(agent_b.agent_id, agent_b.public_key_b64)
    leaf = create_envelope(identity=agent_b, intent=make_intent("search flights", "agent_b"), parent=root)
    repo.save_envelope(leaf)

    def _read_chain():
        return [e.envelope_id for e in repo.get_chain(leaf.envelope_id)]

    expected = [root.envelope_id, leaf.envelope_id]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _read_chain(), range(16)))

    assert all(r == expected for r in results)
