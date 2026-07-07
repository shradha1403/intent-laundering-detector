"""
BrokerService: the one place that ties identity, envelopes, the
interceptor, storage, and the fidelity engine together.

This is deliberately framework-agnostic - it's plain Python with no
FastAPI imports, so it can be exercised directly in tests and demo
scripts without going through HTTP, and the FastAPI app in
aui/broker/app.py is just a thin routing layer on top of it. That
split matters for a live demo: if the network hiccups, the
underlying logic still runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from aui.crypto.keys import AgentIdentity
from aui.envelope.chain import create_envelope, verify_chain
from aui.envelope.schema import Envelope, Intent, ActionReceipt
from aui.fidelity.engine import FidelityEngine
from aui.interceptor.tools import call_tool
from aui.storage.repository import EnvelopeRepository


@dataclass
class BrokerService:
    repo: EnvelopeRepository = field(default_factory=EnvelopeRepository)
    engine: FidelityEngine = field(default_factory=FidelityEngine)
    _identities: dict[str, AgentIdentity] = field(default_factory=dict)

    # ---- agent lifecycle --------------------------------------------------
    def register_agent(self, agent_id: str) -> AgentIdentity:
        identity = AgentIdentity.generate(agent_id)
        self._identities[agent_id] = identity
        # Private key is persisted alongside the public key, not just
        # kept in-memory, so a restarted or second BrokerService can
        # still sign on this agent's behalf. See AgentKeyRow's docstring
        # in aui/storage/models.py for exactly what this trades away.
        self.repo.register_key(agent_id, identity.public_key_b64, identity.private_key_b64)
        return identity

    def _get_identity(self, agent_id: str) -> Optional[AgentIdentity]:
        """Look up an agent's signing identity, checking the in-memory
        cache first and falling back to reloading it from the persisted
        private key if this process didn't register the agent itself
        (e.g. a restart, or a second BrokerService over the same DB).
        Found necessary by a security audit that spun up a second
        BrokerService over the same DB and got `unknown agent` on an
        agent the first instance had already registered - that failure
        is exactly what this fallback closes."""
        identity = self._identities.get(agent_id)
        if identity is not None:
            return identity

        private_key_b64 = self.repo.get_private_key(agent_id)
        if private_key_b64 is None:
            return None
        identity = AgentIdentity.from_private_key_b64(agent_id, private_key_b64)
        self._identities[agent_id] = identity
        return identity

    # ---- envelope creation --------------------------------------------------
    def create_envelope(
        self,
        agent_id: str,
        intent: Intent,
        parent_envelope_id: Optional[str] = None,
    ) -> Envelope:
        identity = self._get_identity(agent_id)
        if identity is None:
            raise ValueError(f"unknown agent {agent_id!r}, call register_agent first")

        parent = self.repo.get_envelope(parent_envelope_id) if parent_envelope_id else None
        envelope = create_envelope(identity=identity, intent=intent, parent=parent)
        self.repo.save_envelope(envelope)

        if parent is not None:
            score, flags = self.engine.pairwise(parent, envelope)
            passed = not flags
            self.repo.save_fidelity_score(
                envelope.envelope_id, "pairwise", score, self.engine.thresholds.pairwise(intent.structured.resource), passed, flags
            )
            envelope.fidelity.pairwise_score = score
            envelope.fidelity.flags.extend(flags)
            self.repo.update_envelope(envelope)

        return envelope

    # ---- action execution (must go through here, see interceptor/tools.py) --
    def execute_action(self, envelope_id: str, tool_name: str, arguments: dict) -> Envelope:
        envelope = self.repo.get_envelope(envelope_id)
        if envelope is None:
            raise ValueError(f"unknown envelope {envelope_id!r}")

        result_summary = call_tool(tool_name, arguments)
        envelope.action_receipt = ActionReceipt(tool_name=tool_name, arguments=arguments, result_summary=result_summary)

        score, flags = self.engine.action_grounding(envelope)
        threshold = self.engine.thresholds.action_grounding_default
        self.repo.save_fidelity_score(envelope_id, "action", score, threshold, not flags, flags)
        envelope.fidelity.action_grounding_score = score
        envelope.fidelity.flags.extend(flags)

        self.repo.update_envelope(envelope)
        return envelope

    # ---- verification --------------------------------------------------------
    def verify_transitive(self, leaf_envelope_id: str) -> tuple[float, list[str]]:
        chain = self.repo.get_chain(leaf_envelope_id)
        if not chain:
            raise ValueError(f"unknown envelope {leaf_envelope_id!r}")
        root, leaf = chain[0], chain[-1]

        # threshold comes back from engine.transitive() itself rather than
        # being recomputed here. It used to be recomputed from
        # leaf.intent.structured.resource - the agent's own self-declared
        # resource, the exact field the engine deliberately does not
        # trust for this check (see actual_resource_touched in
        # engine.py). That meant the threshold recorded in the ledger
        # could disagree with the one the engine actually decided
        # pass/fail against. Found during a security audit, fixed by
        # making the engine the single source of truth for its own
        # threshold instead of letting the caller guess it a second way.
        score, flags, threshold = self.engine.transitive(root, leaf)
        self.repo.save_fidelity_score(leaf.envelope_id, "transitive", score, threshold, not flags, flags)

        leaf.fidelity.transitive_score = score
        leaf.fidelity.flags.extend(flags)
        self.repo.update_envelope(leaf)
        return score, flags

    def verify_chain_integrity(self, leaf_envelope_id: str) -> list[str]:
        """Cryptographic tamper check: hash links + signatures, independent
        of anything semantic. This is the 'has this record been altered
        after the fact' question, separate from 'does this record show
        laundering'."""
        chain = self.repo.get_chain(leaf_envelope_id)
        keys = self.repo.all_public_keys()
        return verify_chain(chain, keys)

    def get_chain(self, leaf_envelope_id: str) -> list[Envelope]:
        return self.repo.get_chain(leaf_envelope_id)
