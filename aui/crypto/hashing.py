"""
Canonical JSON + content hashing for envelopes.

Why canonicalize before hashing: if two people serialize the "same"
JSON with different key ordering or whitespace, a naive hash would
give two different results for logically identical content, which
breaks signature verification for no good reason and opens the door
to signature malleability. Sorting keys and using a fixed separator
gives a deterministic byte string for a deterministic hash.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON serialization: sorted keys, no extra whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def content_hash(obj: Any) -> str:
    """SHA-256 of the canonical form, hex-encoded."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()
