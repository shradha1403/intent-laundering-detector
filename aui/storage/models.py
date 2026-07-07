"""
SQLAlchemy models for the envelope ledger.

Design choice: store each envelope as a JSON blob (its full pydantic
representation) plus a handful of indexed columns for the queries we
actually need (lookup by id, walk by parent_id, filter by root_id or
agent_id). This avoids a wide, brittle relational schema for a nested
structure that's naturally document-shaped, while still letting the
chain-walk (`WHERE root_id = ?  ORDER BY created_at`) be a fast
indexed query instead of a recursive join. SQLite is deliberately the
default backend (see docs/ROADMAP.md for the Postgres upgrade path):
zero ops, file-based, and a demo you can hand someone on a laptop.
"""
from __future__ import annotations

from sqlalchemy import Column, String, Text, Float, Boolean, DateTime, JSON
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class EnvelopeRow(Base):
    __tablename__ = "envelopes"

    envelope_id = Column(String, primary_key=True)
    parent_envelope_id = Column(String, index=True, nullable=True)
    root_envelope_id = Column(String, index=True, nullable=False)
    agent_id = Column(String, index=True, nullable=False)
    created_at = Column(String, nullable=False)
    content_hash = Column(String, index=True, nullable=False)
    payload = Column(JSON, nullable=False)  # full Envelope.model_dump()


class AgentKeyRow(Base):
    __tablename__ = "agent_keys"

    agent_id = Column(String, primary_key=True)
    public_key_b64 = Column(String, nullable=False)
    # Nullable, and a real security tradeoff, not a free upgrade: this
    # persists agent PRIVATE key material in the same SQLite file as
    # the ledger itself, so a BrokerService can reload an agent's
    # signing identity after a restart instead of the agent becoming
    # permanently unable to sign (a Critical finding from a security
    # audit - BrokerService._identities was in-memory only, so any
    # process restart or second worker made every previously-registered
    # agent unable to create new envelopes). Storing the private key
    # here is consistent with, not worse than, this MVP's already-
    # documented trust model (the Broker is a single trusted service
    # that can already rewrite the ledger's meaning of "trusted" - see
    # README). A real deployment should keep signing keys in a KMS/HSM
    # that is NOT the same store as the audit ledger, so compromising
    # the ledger doesn't also hand over every agent's signing key.
    # That's not built here; flagging it rather than hiding it.
    private_key_b64 = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())
    revoked_at = Column(String, nullable=True)


class FidelityScoreRow(Base):
    __tablename__ = "fidelity_scores"

    id = Column(String, primary_key=True)  # f"{envelope_id}:{check_type}"
    envelope_id = Column(String, index=True, nullable=False)
    check_type = Column(String, nullable=False)  # pairwise | action | transitive
    score = Column(Float, nullable=False)
    threshold_used = Column(Float, nullable=False)
    passed = Column(Boolean, nullable=False)
    flags = Column(JSON, default=list)
    evaluated_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())
