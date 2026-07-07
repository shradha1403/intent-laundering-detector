# Authority-Use Integrity

Detects intent laundering in multi-agent AI systems: the moment a delegated agent uses authority it legitimately holds for a purpose that no longer matches what the human actually asked for, even though every permission check along the way succeeds.

Regular IAM answers "can this agent do this." This answers "does this action still serve what the human wanted."

## The core idea

Every delegation hop in a multi-agent chain creates a signed, hash-linked **Intent Envelope**: a record of what that agent claims it's doing, chained back to its parent. A verifier checks fidelity at three levels:

1. **Pairwise** - does this hop's stated intent plausibly follow from its parent's? A text-similarity comparison. Read the actual number before trusting this stage too much: an independent audit measured the lexical backend's own floor for maximally unrelated text at 0.13-0.18, which left almost no headroom against the original 0.15 threshold (raised to 0.17 after that finding, see `docs/EVALUATION.md` and the comment on `FidelityThresholds` in `aui/fidelity/engine.py`). This stage is a real signal, not the strongest one.
2. **Action grounding** - does the agent's *actual* tool call match what it *said* it would do? This is the part most "compare two paraphrases" designs skip, and it's the part that actually catches an agent lying about what it's doing. This stage is rule-based (an allowed-tool-per-action-type map, plus a ground-truth resource check), not text-similarity, which is exactly why it resists keyword-stuffing.
3. **Transitive** - compared directly against the *root* intent, not chained pairwise score to pairwise score, does the leaf action still serve the original ask? **Be precise about the mechanism here, because the framing in earlier drafts of this README overstated it**: the actual detection in `aui/fidelity/engine.py::FidelityEngine.transitive` is a categorical check - is the resource the leaf action *actually* touched (grounded in the real tool call, not the agent's self-declared field) a sensitive one the root task never expected? If yes, the score is capped and the hop is flagged, full stop, regardless of how similar the wording sounds. The text-similarity score between root and leaf is a secondary signal on top of that, not the thing carrying the flagship "cumulative drift" scenario. Call this what it is: a hand-authored allowlist of expected sensitive sub-resources per root task (`EXPECTED_SENSITIVE_SUBRESOURCES`), not a semantic model of intent. It's a real, working control, and it's simpler than "detects semantic drift" implies.

That third check is the thing this project is built around: local, hop-to-hop fidelity checks can all pass while the chain as a whole launders intent. `aui/tests/test_fidelity_engine.py::test_transitive_catches_drift_that_survives_every_pairwise_hop` demonstrates exactly this - and demonstrates the categorical mechanism above, not text similarity alone.

Full stage-by-stage writeup (the story, not just the code): `docs/WALKTHROUGH.md`.

## Related work, read this before calling anything here novel

A literature check done after the MVP was built (see `docs/PUBLISHABILITY.md` for the full writeup) found that this problem is not undiscovered: several teams converged on almost exactly this architecture in the first half of 2026. [SentinelAgent](https://arxiv.org/abs/2604.02767) (April 2026) is the closest hit, a formal delegation-chain framework with a proven "intent entailment preservation" property and a pre/at/post-execution verification lifecycle that's architecturally the same shape as this project's pairwise/action-grounding/transitive split, evaluated on a 516-scenario benchmark. [HDP](https://arxiv.org/abs/2604.04522) (April 2026) already standardizes the signed, hash-linked provenance layer this project builds, as an IETF Internet-Draft with a shipping SDK. [Authorization Propagation in Multi-Agent AI Systems](https://arxiv.org/abs/2605.05440) (May 2026) formally separates this class of problem from prompt injection. [Agent Drift](https://arxiv.org/abs/2601.04170) (January 2026) names the core phenomenon "semantic drift."

What this repo is: an independent, from-scratch, open-source implementation of that same problem, with its own red-team findings (two real bugs found and fixed by attacking its own fidelity engine, see `docs/ROADMAP.md`), not a claim to have been first. Cite the above before pitching this as novel research to anyone who might already know that literature.

## What's honest about this MVP

This project went through an independent adversarial audit (a fresh review with instructions to find problems, not credit effort - see `docs/AUDIT.md`) after the initial build. Every reasonably fixable finding from that audit was actually fixed in the code, not just written down; the rest are documented below and in `docs/AUDIT.md` with why they're still open. This section reflects the post-fix state.

The similarity backend used for pairwise/transitive scoring is `LexicalSimilarityBackend` (difflib + word overlap), not a real embedding or NLI model, and not TF-IDF either, on purpose and by measurement. A TF-IDF cosine-similarity backend (scikit-learn, fit on a fixed reference corpus, plus stemming) was built as the planned "upgrade," iteratively debugged through two real bugs, and still scored worse than plain lexical overlap on an 18-case labeled benchmark (0.8 vs 1.0 precision, see `docs/EVALUATION.md` for the full story and `python scripts/evaluate.py` to reproduce it). Shipping the backend that measures better, not the one that sounds fancier. A real embedding model (`sentence-transformers` + a local NLI cross-encoder) was attempted before that and specifically blocked by this authoring environment's inability to install `torch` within its execution limits, not skipped for lack of time. `EmbeddingSimilarityBackend` in `aui/fidelity/backend.py` is the documented swap-in once that constraint doesn't apply, no redesign needed, just a different backend behind the same interface. The audit also measured the lexical backend's discriminative headroom directly and found the original pairwise threshold (0.15) was nearly inert; it's now 0.17, a verified, modest improvement, not a claim that this stage is strong (see the transitive-check note above for why the categorical checks, not text similarity, do the real work here).

The Broker is a single trusted service. If it's compromised, it can rewrite the ledger's meaning of "trusted." A real deployment would want a transparency-log pattern (publish Merkle roots externally, like Certificate Transparency) so even the Broker can't quietly rewrite history undetected. Not built for the MVP, noted here on purpose. The FastAPI layer now requires a bearer token (`AUI_API_KEY`) on every route, closing an audit finding that the ledger had unauthenticated write access from anyone who could reach the port - but that's an API-access gate, not agent attestation, see the next paragraph.

Agent keys are self-registered, not attested. Anyone who can call `register_agent` (with a valid API token, now) can mint an identity. Fine for a demo, not fine for production; hardware-backed keys or mTLS client certs would be the real fix, and that's still not built - it needs infrastructure this MVP doesn't have, tracked honestly in `docs/AUDIT.md` rather than pretended away. One thing that IS fixed: agent private keys are now persisted (not just the public key), so a restarted broker process can still sign on a previously-registered agent's behalf - an audit found the API was unusable across any restart before this, since `BrokerService` kept private keys in memory only. Persisting private keys in the same store as the ledger is itself a real tradeoff, spelled out in `aui/storage/models.py`.

## Quickstart

```bash
pip install -r requirements.txt

# run the test suite (35 tests: crypto/chain integrity, storage, fidelity
# engine, adversarial red-team cases, dashboard smoke tests, the FastAPI
# layer with auth, tool-map consistency, broker-restart survival, and
# concurrent-access checks added after an independent audit, see docs/AUDIT.md)
pytest -v

# run the three-scenario demo (normal delegation, action-mismatch laundering,
# cumulative-drift laundering) end to end, prints forensic reports for each
python scripts/demo.py

# run the 18-case labeled benchmark, compares the lexical and TF-IDF
# backends head to head with precision/recall/FPR/accuracy (see docs/EVALUATION.md)
python scripts/evaluate.py

# or the same three scenarios, live and visual
streamlit run scripts/dashboard.py

# or run the broker as a real API
uvicorn aui.broker.app:app --reload
# docs at http://localhost:8000/docs
# every route needs `Authorization: Bearer <token>` - set AUI_API_KEY
# yourself, or read the one-time token this prints to stdout on startup
# also configurable: AUI_DB_URL (defaults to sqlite:///aui_ledger.db)
```

Or with Docker, one command for both the API and the dashboard:

```bash
docker compose up --build
# broker API   -> http://localhost:8000/docs
# dashboard    -> http://localhost:8501
```

## Repo layout

```
aui/
  envelope/     the Intent Envelope schema + hash-chain build/verify
  crypto/       Ed25519 signing, canonical-JSON hashing
  fidelity/     the 3-stage verification engine + pluggable similarity backend
  storage/      SQLAlchemy models + repository (SQLite by default)
  interceptor/  the only path any agent has to a real tool call
  broker/       BrokerService (framework-agnostic) + FastAPI wrapper
  agents/       minimal hand-rolled agent harness (no LangGraph/CrewAI, see docs/ROADMAP.md)
  forensics/    chain reconstruction -> human-readable incident report
  demo_scenarios.py   the 3 canonical scenarios, shared by the CLI and the dashboard
  benchmark.py  18 labeled cases (benign vs. laundering) across 4 domains, used by scripts/evaluate.py
  tests/        pytest suite, including the tests that encode the core research claim,
                the adversarial red-team cases in test_adversarial.py, and (added after
                an independent audit) test_broker_api.py, test_broker_restart.py,
                test_consistency.py, and test_concurrency.py
scripts/
  demo.py       the three-scenario CLI demo
  dashboard.py  the same three scenarios, live and visual (streamlit run scripts/dashboard.py)
  evaluate.py   runs aui/benchmark.py against both similarity backends, prints precision/recall/FPR
docs/
  ROADMAP.md    day-by-day build plan and what's still v2
  EVALUATION.md the benchmark design, the two real bugs found building the TF-IDF backend, and
                why the simpler lexical backend still shipped as the default
  AUDIT.md      an independent adversarial audit, every fix that came out of it with diffs
                and verification, and what's still open with why
```

## Why these tradeoffs

Full reasoning for every architecture and stack decision, plus the MVP scope cuts, adversarial threat model, and publishability gaps, is in `docs/ROADMAP.md` and was worked through as a design review before any code was written. Short version: this system claims to detect the compounding version of a confused-deputy attack for agent delegation chains, it borrows provenance/signing patterns from in-toto and verifiable credentials rather than reinventing them, and its own semantic verifier is treated as a heuristic signal feeding a policy decision, not a security boundary in itself.
