from __future__ import annotations

from typing import Optional

from aui.envelope.schema import Envelope
from aui.storage.db import get_session
from aui.storage.models import EnvelopeRow, AgentKeyRow, FidelityScoreRow


class EnvelopeRepository:
    """All persistence for envelopes, agent keys, and fidelity scores.

    Kept as a thin repository class (rather than scattering raw
    SQLAlchemy calls through the Broker) so the Broker's HTTP layer
    stays about routing and validation, not persistence details.
    """

    # ---- agent identity -----------------------------------------------
    def register_key(self, agent_id: str, public_key_b64: str) -> None:
        with get_session() as s:
            existing = s.get(AgentKeyRow, agent_id)
            if existing:
                existing.public_key_b64 = public_key_b64
            else:
                s.add(AgentKeyRow(agent_id=agent_id, public_key_b64=public_key_b64))

    def get_public_key(self, agent_id: str) -> Optional[str]:
        with get_session() as s:
            row = s.get(AgentKeyRow, agent_id)
            return row.public_key_b64 if row else None

    def all_public_keys(self) -> dict[str, str]:
        with get_session() as s:
            rows = s.query(AgentKeyRow).all()
            return {r.agent_id: r.public_key_b64 for r in rows}

    # ---- envelopes ------------------------------------------------------
    def save_envelope(self, envelope: Envelope) -> None:
        with get_session() as s:
            s.add(
                EnvelopeRow(
                    envelope_id=envelope.envelope_id,
                    parent_envelope_id=envelope.parent_envelope_id,
                    root_envelope_id=envelope.root_envelope_id,
                    agent_id=envelope.agent_id,
                    created_at=envelope.created_at,
                    content_hash=envelope.provenance.content_hash,
                    payload=envelope.model_dump(),
                )
            )

    def update_envelope(self, envelope: Envelope) -> None:
        """Used after attaching an action_receipt or fidelity results."""
        with get_session() as s:
            row = s.get(EnvelopeRow, envelope.envelope_id)
            if row is None:
                raise KeyError(f"envelope {envelope.envelope_id} not found")
            row.payload = envelope.model_dump()

    def get_envelope(self, envelope_id: str) -> Optional[Envelope]:
        with get_session() as s:
            row = s.get(EnvelopeRow, envelope_id)
            return Envelope.model_validate(row.payload) if row else None

    def get_chain(self, leaf_envelope_id: str) -> list[Envelope]:
        """Walk parent_envelope_id pointers from a leaf back to its root,
        return root-to-leaf ordered. This is a straightforward linked-list
        walk, fine at demo scale; at real scale with branching/merging
        this is where a proper graph traversal (recursive CTE or a graph
        DB) would replace the loop - see docs/ROADMAP.md v2 notes.
        """
        chain: list[Envelope] = []
        current_id: Optional[str] = leaf_envelope_id
        with get_session() as s:
            while current_id is not None:
                row = s.get(EnvelopeRow, current_id)
                if row is None:
                    break
                chain.append(Envelope.model_validate(row.payload))
                current_id = row.parent_envelope_id
        return list(reversed(chain))

    def get_root(self, envelope_id: str) -> Optional[Envelope]:
        with get_session() as s:
            row = s.get(EnvelopeRow, envelope_id)
            if row is None:
                return None
            root_row = s.get(EnvelopeRow, row.root_envelope_id)
            return Envelope.model_validate(root_row.payload) if root_row else None

    # ---- fidelity scores --------------------------------------------------
    def save_fidelity_score(
        self, envelope_id: str, check_type: str, score: float, threshold: float, passed: bool, flags: list[str]
    ) -> None:
        with get_session() as s:
            row_id = f"{envelope_id}:{check_type}"
            existing = s.get(FidelityScoreRow, row_id)
            if existing:
                existing.score = score
                existing.threshold_used = threshold
                existing.passed = passed
                existing.flags = flags
            else:
                s.add(
                    FidelityScoreRow(
                        id=row_id,
                        envelope_id=envelope_id,
                        check_type=check_type,
                        score=score,
                        threshold_used=threshold,
                        passed=passed,
                        flags=flags,
                    )
                )

    def get_fidelity_scores(self, envelope_id: str) -> list[dict]:
        with get_session() as s:
            rows = s.query(FidelityScoreRow).filter_by(envelope_id=envelope_id).all()
            return [
                {
                    "check_type": r.check_type,
                    "score": r.score,
                    "threshold_used": r.threshold_used,
                    "passed": r.passed,
                    "flags": r.flags,
                }
                for r in rows
            ]
