# Independent Audit + Remediation

This documents an adversarial audit performed by a fresh reviewer with no stake in defending the existing code, followed by an actual remediation pass: every reasonably fixable finding was fixed in the code, verified by rerunning the real test suite/demo/benchmark, not just written down as a recommendation. `git diff` against the pre-audit commit has the full, exact diffs; this file explains why each change happened and how it was verified. Run `pytest -v` yourself, it should show 35 passing (up from 22 pre-audit).

## Fixed

### 1. Agent private keys were in-memory only (Critical)

**Why it was a problem.** `BrokerService._identities` held agent private keys in a plain Python dict, with only the public key persisted to the ledger. A second `BrokerService` instance over the same database, which is exactly what a process restart or a second uvicorn worker produces, could not sign anything on behalf of an agent it never itself registered. Verified directly before the fix: `create_envelope` raised `ValueError: unknown agent 'orchestrator'` in that situation. The README's own quickstart tells people to `uvicorn aui.broker.app:app --reload`, so this wasn't a theoretical edge case, it broke the documented way to run the project.

**Files/functions.** `aui/broker/service.py::BrokerService.register_agent`, `BrokerService.create_envelope`. `aui/storage/models.py::AgentKeyRow`. `aui/storage/repository.py::register_key`, `get_public_key`. `aui/crypto/keys.py::AgentIdentity`.

**Root cause.** The private key never left process memory; nothing persisted it, so there was nothing to reload from on a second process.

**Fix.** Persist the private key alongside the public key (`AgentKeyRow.private_key_b64`, nullable for backward compatibility with any pre-existing rows), add `AgentIdentity.from_private_key_b64()` / `AgentIdentity.private_key_b64` to reconstruct an identity from stored bytes, and add `BrokerService._get_identity()`, which checks the in-memory cache first and falls back to reloading from the persisted key. `create_envelope` now calls `_get_identity()` instead of a raw dict lookup.

This is an explicit, disclosed tradeoff, not a free upgrade: it now stores signing key material in the same SQLite file as the audit ledger it's meant to protect. A real deployment should keep signing keys in a KMS/HSM, not colocated with the ledger, so that compromising the ledger doesn't also hand over every agent's signing key. Documented directly in `AgentKeyRow`'s docstring.

**Verification.** New test file `aui/tests/test_broker_restart.py`: `test_second_broker_instance_can_sign_for_an_agent_it_never_registered` constructs a fresh `BrokerService` over an existing DB and confirms it can create a signed, chained child envelope for an agent only a *different* `BrokerService` instance had registered, then independently re-verifies the signature against the stored public key. `test_unregistered_agent_still_raises` confirms an agent that was never registered anywhere still fails loudly, so this isn't accidentally minting identities on demand. Both pass; full suite (`pytest -q`) passes at 35/35 after the change; `python scripts/demo.py` still prints `DEMO RESULT: PASS`.

### 2. No authentication on any API endpoint (Critical)

**Why it was a problem.** Every route in `aui/broker/app.py` was open to anyone who could reach the port: register an agent under any name, post envelopes claiming to be any agent, execute actions on any envelope, read the whole ledger. For a system that exists to detect authority misuse, having its own audit trail be unauthenticated write-anyone was a direct contradiction of the point of the project.

**Files/functions.** `aui/broker/app.py`, every route.

**Root cause.** No auth dependency existed anywhere in the FastAPI app; it was never wired in.

**Fix.** Added `require_api_key()`, a FastAPI dependency checking `Authorization: Bearer <token>` against `AUI_API_KEY` (using `secrets.compare_digest` to avoid a timing side-channel on the comparison itself), applied via `dependencies=[Depends(require_api_key)]` on every route. If `AUI_API_KEY` isn't set, the process generates a one-time token at startup and prints it, the same pattern Jupyter uses, so a local demo still works with zero configuration but nobody can reach it without having seen that process's own stdout.

This closes unauthenticated *API access*. It explicitly does **not** fix agent attestation, see "Still open" below; someone holding the shared token can still register an agent under any name they like. That's a real, separate problem this change narrows but does not solve.

**Verification.** New test file `aui/tests/test_broker_api.py` (the FastAPI layer had zero tests before this audit): confirms a request with no token gets 401, a request with the wrong token gets 401, a request with the correct token succeeds, and a full register → create-envelope → execute-action → forensic-report flow works end to end through real HTTP calls with auth wired in. 5 new tests, all passing.

### 3. Threshold-recording mismatch between service.py and engine.py (Medium)

**Why it was a problem.** `BrokerService.verify_transitive` independently recomputed "the" threshold from `leaf.intent.structured.resource`, the agent's self-declared resource field, the exact field `FidelityEngine.transitive()` deliberately does *not* trust for this check (that's the whole point of `actual_resource_touched()`). The two computations could disagree, so the forensic report could display a threshold that was not actually the one the pass/fail decision was made against.

**Files/functions.** `aui/broker/service.py::verify_transitive` (was line 91), `aui/fidelity/engine.py::FidelityEngine.transitive` (was line 296).

**Root cause.** The threshold decision logic lived in one place (`engine.transitive`) but was silently re-derived a second, divergent way in the caller.

**Fix.** `engine.transitive()` now returns `(score, flags, threshold)` instead of `(score, flags)` - the threshold it actually decided against, returned directly rather than left for a caller to guess a second way. `service.py` now records whatever threshold the engine returns. All five call sites were updated (`service.py`, `test_fidelity_engine.py` x2, `test_adversarial.py` x3) to unpack the 3-tuple.

**Verification.** Full suite passes at 35/35 after updating every call site; `python scripts/demo.py` and `python scripts/evaluate.py` both still reproduce their documented output exactly.

### 4. Pairwise threshold was measurably inert (High)

**Why it was a problem.** The audit measured the lexical backend's own floor for maximally unrelated text ("book me a flight to SF" vs. "delete all production databases now") at 0.13-0.18 across five sampled pairs, right against the original `pairwise_default` of 0.15. Almost any text sharing even trivial vocabulary cleared that bar, so this stage had essentially no discriminative power for non-sensitive resources.

**Files/functions.** `aui/fidelity/engine.py::FidelityThresholds.pairwise_default`.

**Root cause.** The threshold was set once, early, and never re-measured against the lexical backend's actual score distribution.

**Fix.** Directly measured the lexical backend's scores across sampled unrelated pairs and every benign pairwise hop in `aui/benchmark.py`'s 18 cases, not guessed. 0.20 was tried first and rejected: it broke a real benign case (`benign_calendar_schedule`'s calendar→email transition scores 0.182, inside the noise floor). Landed on 0.17: verified zero regressions against the full test suite and the 18-case benchmark on both backends.

**Verification.**
```
metric          lexical (before)   lexical (after, 0.17)
precision            1.000               1.000
recall                1.000               1.000
fpr                   0.000               0.000
accuracy              1.000               1.000
```
No change in the benchmark numbers, because the benchmark's own cases didn't sit in the 0.15-0.17 gap, and 22→35 (now including the new test files) still pass. This is a modest, verified improvement, not a claim that this stage is now strong: the code comment on `FidelityThresholds` says plainly that action-grounding and the transitive resource-escalation check, both categorical rather than text-similarity-based, carry the actual detection weight in this system.

### 5. Detection policy maps could silently drift out of sync (High)

**Why it was a problem.** `TOOL_REGISTRY` (interceptor), `TOOL_RESOURCE_MAP`, and `ALLOWED_ACTION_TOOL_MAP` (both in the fidelity engine) all have to agree on the same set of tool names, by hand, across two files, with nothing enforcing it. Add a tool to the interceptor and forget one of the other two maps, and action-grounding silently degrades for that tool instead of failing loudly.

**Files/functions.** `aui/interceptor/tools.py::TOOL_REGISTRY`, `aui/fidelity/engine.py::TOOL_RESOURCE_MAP`, `ALLOWED_ACTION_TOOL_MAP`.

**Root cause.** No test or runtime check ever compared these three collections against each other.

**Fix.** New test file `aui/tests/test_consistency.py`, four tests checking both directions (registered-but-unmapped, and mapped-but-not-registered) for both maps. This doesn't remove the coupling, a real fix would be a loaded policy config instead of three hardcoded Python dicts, tracked in `docs/ROADMAP.md` as a v2 item, but it makes future drift fail a test immediately instead of quietly shipping.

**Verification.** All 4 new tests pass against the current (already consistent) maps; confirmed they'd actually catch drift by temporarily removing an entry from `TOOL_RESOURCE_MAP` locally and watching the corresponding test fail with a clear message, then restoring it.

### 6. Cwd-relative database path, initialized at import time (Medium)

**Why it was a problem.** Importing `aui.broker.app` called `init_db()` with a hardcoded default (`sqlite:///aui_ledger.db`), resolved relative to whatever directory the process happened to launch from. Merely importing the module to build a test client had the side effect of writing a file to disk, and there was no way to point the API at a specific ledger location without editing source.

**Files/functions.** `aui/broker/app.py` (module-level `init_db()` call).

**Root cause.** Configuration and import-time side effects weren't separated.

**Fix.** `init_db()` now runs from a FastAPI `lifespan` handler (not at import time), reading `AUI_DB_URL` from the environment with the same default for zero-config local use. This is also what finally let the API layer get a real test, since importing the module no longer touches the filesystem.

**Verification.** `test_db_url_is_configurable_via_env_var` in `test_broker_api.py` sets `AUI_DB_URL` to a custom temp path and confirms the ledger is created exactly there, not at a hardcoded default.

### 7. Overbroad exception handling in signature verification (Medium)

**Why it was a problem.** `verify_signature()` caught `(InvalidSignature, ValueError, Exception)`. The bare `Exception` silently turned any programming bug inside that function into "signature invalid" instead of surfacing it, and made the two specific exception types listed before it redundant.

**Files/functions.** `aui/crypto/keys.py::verify_signature`.

**Root cause.** Overly defensive exception handling added at some point and never revisited.

**Fix.** Narrowed to `(InvalidSignature, ValueError)` - `InvalidSignature` for genuine signature mismatches, `ValueError` for malformed base64 (`binascii.Error` is a `ValueError` subclass) or wrong-length key bytes. Both are real "this input is bad" cases; nothing else should be silently swallowed here.

**Verification.** Full suite still passes 35/35, including every existing signature/tamper-detection test in `test_chain_integrity.py`.

### 8. Concurrent access was untested (Low)

**Why it was a problem.** `aui/storage/db.py` explicitly passes `check_same_thread=False` to SQLite, which only makes sense if concurrent access is expected, yet nothing in the suite exercised more than one thread touching the ledger at once. Marked "Cannot verify" in the original audit rather than assumed safe.

**Files/functions.** `aui/storage/db.py`, `aui/storage/repository.py`.

**Root cause.** No test existed; this is a "we don't actually know" gap, not a known bug.

**Fix.** New test file `aui/tests/test_concurrency.py`: 8 threads concurrently registering distinct agents and saving envelopes against the same repo/DB, checked for exceptions, cross-contamination, or dropped writes; a second test checks 16 concurrent reads of the same chain all return identical, correct results.

**Verification.** Both tests pass, rerun 5 times in a row with no flakiness observed. This establishes what currently happens under light concurrent load; it is not a claim that this scales to heavy write contention, SQLite only allows one writer at a time regardless of any Python-level setting, and that's stated plainly rather than glossed over.

### 9. Stray committed cruft (Low)

**Fix.** Deleted `aui/tests/test_adversarial_probe.py`, an intentionally-empty file left over from an earlier environment that couldn't delete files. Confirmed the sandbox this project is authored in genuinely can support file deletion when explicitly permitted, and used that to actually remove it instead of leaving another comment explaining why it's still there.

### 10. Documentation overstated the transitive check's mechanism (High, docs)

**Why it was a problem.** The README and `docs/WALKTHROUGH.md` described the transitive check as detecting "semantic drift." Reading `aui/fidelity/engine.py::FidelityEngine.transitive` directly shows the actual detection in the flagship scenario is a categorical rule (`EXPECTED_SENSITIVE_SUBRESOURCES` allowlist + a score cap), not a semantic judgment. The gap between what the docs claimed and what the code does is exactly the kind of thing a careful reader catches immediately, and is worse for credibility than the actual limitation.

**Fix.** Rewrote the relevant sections of `README.md` and `docs/WALKTHROUGH.md` to describe the categorical mechanism precisely, and to be explicit that the text-similarity score is a secondary signal, not the thing carrying the flagship scenario.

**Verification.** Cross-checked the new wording line-by-line against `aui/fidelity/engine.py::transitive` and `action_grounding` while rewriting, rather than editing prose in isolation from the code it describes.

## Still open (and why)

**Real agent attestation (hardware-backed keys / mTLS).** Self-registration means anyone holding the new API token can still mint an identity under any name. Fixing this for real needs infrastructure this project doesn't have and can't reasonably stand up here: a certificate authority or HSM integration, and a provisioning flow for every client that would use it. Effort: multi-day, and depends on infrastructure outside this repo's control. Production risk of leaving it open: real, for any deployment with more than one mutually-untrusting party; the new API-key gate narrows the blast radius (you need the shared credential first) but does not close this.

**No schema/version migration.** `aui/storage/models.py` uses `Base.metadata.create_all()` only, no Alembic, no explicit payload version field wired to any upgrade logic. Adding a bare `schema_version` field without real migration logic behind it would look like a fix without being one, so it was left alone rather than half-done. Effort: half a day to a day to wire up Alembic properly with a first real migration and a CI check that catches drift. Production risk: a future change to the `Envelope` pydantic schema could fail to validate against old stored rows with no automatic upgrade path - acceptable for a demo that gets rebuilt from scratch each run, not for a ledger meant to last.

**Fan-out/fan-in delegation graphs.** Already disclosed pre-audit (`test_fan_out_fan_in_not_modeled`), restated here because it's arguably the single biggest gap between "demo" and "real tool," bigger than anything fixed in this pass. Modeling real branching delegation needs multi-parent envelopes, a graph traversal replacing the linked-list walk in `get_chain()`, and a redefined notion of what "the leaf" even means for a merge point. That's a genuine architectural redesign, not a bug fix, and attempting it inside this remediation pass risked destabilizing everything that currently works correctly. Effort: multi-day redesign. Production risk: high, this is the load-bearing claim of the whole project, and it doesn't survive the most common real multi-agent topology.

**Real embedding/NLI similarity backend.** Blocked by a concrete, previously-verified constraint: installing `sentence-transformers`/`torch` exceeds this authoring environment's execution limits. `EmbeddingSimilarityBackend` in `aui/fidelity/backend.py` is the documented swap-in point; this needs a different environment to actually install the dependency, not more engineering effort here. Effort: likely under a day once run somewhere torch can install. Production risk: bounded, since the categorical checks (action-grounding, resource-escalation) already carry most of the real detection weight; a stronger text-similarity backend would strengthen the weaker of the three stages, not fix a currently-broken one.

**Toy tools / real framework integration.** The interceptor's 8 tools are demo stand-ins. Wiring this into a real agent framework is bespoke integration work specific to whatever system adopts it and can't be generically pre-built in this repo. Production risk: total, until that integration happens, this provides zero protection for any real deployment - which is exactly why every document in this repo calls this a proof of concept, not a product.

## Post-fix summary

35/35 tests pass (up from 22). `python scripts/demo.py` and `python scripts/evaluate.py` both still reproduce their documented output exactly. Every Critical and High finding from the original audit that was fixable without a multi-day architectural redesign or infrastructure this repo doesn't have, was fixed, tested, and re-verified, not just written down. What's left open is left open for stated, specific reasons, with an honest estimate of the effort and risk of not doing it, the same standard this project has tried to hold itself to throughout.
