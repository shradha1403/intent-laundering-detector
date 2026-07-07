"""
Ed25519 identity + signing for agents.

Design decision: every agent instance gets its own keypair at spawn
time. This is what makes the forensic trail non-repudiable - you can
prove which specific agent signed which specific claim. For the MVP,
keys are self-registered (an agent generates its own keypair and
hands the Broker its public key). That's a known weak point: it's
not attestation, anyone who can talk to the Broker can mint an
identity. Real deployments would want hardware-backed keys or mTLS
client certs. Flagging this honestly rather than pretending the MVP
solves it.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


@dataclass
class AgentIdentity:
    """A generated Ed25519 keypair representing one agent instance."""

    agent_id: str
    _private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls, agent_id: str) -> "AgentIdentity":
        return cls(agent_id=agent_id, _private_key=Ed25519PrivateKey.generate())

    @classmethod
    def from_private_key_b64(cls, agent_id: str, private_key_b64: str) -> "AgentIdentity":
        """Reconstruct an identity from a persisted private key, so a
        BrokerService can reload an agent that was registered in a
        previous process (see aui/storage/models.py::AgentKeyRow for why
        this is now persisted, and the tradeoff that comes with it)."""
        raw = _unb64(private_key_b64)
        return cls(agent_id=agent_id, _private_key=Ed25519PrivateKey.from_private_bytes(raw))

    @property
    def private_key_b64(self) -> str:
        raw = self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return _b64(raw)

    @property
    def public_key_b64(self) -> str:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return _b64(raw)

    def sign(self, content_hash_hex: str) -> str:
        """Sign the hex-encoded content hash of an envelope. Returns base64 signature."""
        sig = self._private_key.sign(content_hash_hex.encode("ascii"))
        return _b64(sig)


def verify_signature(public_key_b64: str, content_hash_hex: str, signature_b64: str) -> bool:
    """Verify a signature against a public key and content hash.

    Returns False on any failure (bad key, bad signature, tampered hash)
    rather than raising, since callers just need a yes/no for chain
    validation.
    """
    try:
        pub_bytes = _unb64(public_key_b64)
        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(_unb64(signature_b64), content_hash_hex.encode("ascii"))
        return True
    except (InvalidSignature, ValueError):
        # InvalidSignature: signature genuinely doesn't match (tampering,
        # wrong key). ValueError: malformed base64 (binascii.Error is a
        # ValueError subclass) or wrong-length key bytes. Both are real
        # "this input is bad" cases, which is what callers need a False
        # for. Previously this also caught bare Exception, which silently
        # turned any programming bug in this function into "signature
        # invalid" instead of surfacing it - found during the security
        # audit, not by a failing test, since nothing here happened to
        # throw anything else yet.
        return False
