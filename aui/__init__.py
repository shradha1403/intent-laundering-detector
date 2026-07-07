"""
Authority-Use Integrity (AUI)

Detects intent laundering in multi-agent AI systems: the moment a
delegated agent uses authority it legitimately holds for a purpose
that no longer matches the original human intent, even though every
permission check along the way succeeded.

Core primitive: the Intent Envelope. Every delegation hop signs a
hash-linked envelope restating its sub-intent. A verifier checks
fidelity at three levels:
  1. pairwise    - does this hop's stated intent follow from its parent's?
  2. action      - does the actual tool call this agent made match what
                   it *said* it was going to do? (the part most designs
                   like this skip, and the part that actually matters)
  3. transitive  - does the leaf, compared directly against the root,
                   still serve the original human intent? (catches
                   gradual drift that no single hop would ever flag)
"""

__version__ = "0.1.0"
