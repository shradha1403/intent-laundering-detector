# Roadmap

## Status as of today

Built and passing (22/22 tests, live demo and dashboard both run end to end):

- Intent Envelope schema, Ed25519 signing, canonical-JSON hashing, hash-chain build + verify
- SQLite-backed ledger (envelopes, agent keys, fidelity scores) via a repository layer
- 3-stage Fidelity Engine: pairwise, action-grounding (rule-based + text sanity check), transitive
- Action interceptor + 8 toy tools, the only path any simulated agent has to "do" anything
- BrokerService (framework-agnostic) + a thin FastAPI wrapper with OpenAPI docs
- Forensic reconstruction: chain walk -> human-readable incident report
- `aui/demo_scenarios.py`: the 3 canonical scenarios, shared by both entry points below
- `scripts/demo.py`: CLI run of all three scenarios, prints forensic reports, pass/fail summary
- `scripts/dashboard.py`: the same three scenarios, live in Streamlit, scenario picker + chain-as-cards view + a laundering banner + a side-by-side against what a traditional IAM audit log would show. Smoke-tested with Streamlit's `AppTest` (`aui/tests/test_dashboard_smoke.py`), no browser needed to verify it runs and shows the right banner per scenario.
- `aui/tests/test_adversarial.py`: a first red-team pass, see below
- `aui/benchmark.py` + `scripts/evaluate.py`: an 18-case labeled benchmark (benign vs. laundering, 4 domains) comparing the lexical backend against a TF-IDF backend on precision/recall/FPR, see `docs/EVALUATION.md`

The demo currently proves the core claim end to end: scenario 3 constructs a chain where every individual pairwise hop passes, and only the transitive (root-vs-leaf) check catches the drift. That's the load-bearing test in `aui/tests/test_fidelity_engine.py`.

## Red-team pass, done (chat phase 9), what it found

Went looking for ways to fool the verifier before building a UI on top of it. Found two real, previously-invisible bugs, both by actually running constructed attacks rather than reasoning about them in the abstract:

1. **Resource self-declaration bypass.** The transitive escalation check originally trusted the agent's own `structured.resource` field. An attacker could declare a benign resource while its real tool call touched a sensitive one (e.g. declare `account_settings`, actually call `add_recovery_contact`), and the check never fired because it was checking a claim, not a fact. Fixed by grounding both action-grounding and the transitive check in `actual_resource_touched()`, which derives the real resource from the tool that was actually called (`TOOL_RESOURCE_MAP` in `aui/fidelity/engine.py`), not what the agent says about itself.

2. **That fix immediately over-corrected into a false positive.** Grounding the resource check in reality meant a completely benign flight purchase (`charge_card`, ground-truth resource `payment_api`) started getting flagged as "escalation" relative to the `flight_booking` root, because paying for a flight touches a different, sensitive-category resource than the root nominally declared, even though that's an expected part of the task. Fixed with `EXPECTED_SENSITIVE_SUBRESOURCES`, an explicit, hand-authored allowlist of which sensitive sub-resources are a normal consequence of a given root task (flight/hotel booking legitimately touches payment; account settings does NOT legitimately touch adding a new recovery contact). This is a blunt stand-in for what should eventually be a learned or policy-defined model, and it's an allowlist on purpose: anything not explicitly listed defaults to "treat as escalation," which is the safer failure direction for a security control.

Both are now permanent regression tests in `test_adversarial.py`, alongside a keyword-stuffing attack (rule-based action-type check isn't fooled by text games), a vague-root-intent attempt (the escalation check is categorical, not text-similarity based, so vagueness doesn't help the attacker), and a boundary-hugging multi-hop attack (every pairwise hop crafted to individually pass, transitive still catches it).

One gap found and **deliberately left unfixed**, documented instead: `test_fan_out_fan_in_not_modeled` confirms the architecture assumes a single linear parent per envelope. A real fan-out/fan-in delegation (one root spawns parallel children, a downstream agent merges results from more than one branch) has no representation here. If laundering happens on one branch and a merge point only cites the clean branch as its declared parent, that branch is invisible to forensic reconstruction. This needs multi-parent envelopes or a real graph model, tracked as a v2 item, not something the current linear-chain design can absorb without a real redesign of `get_chain()`.

## Day 4, done: labeled benchmark + backend evaluation

Built `aui/benchmark.py` (18 hand-labeled cases, 10 benign / 8 laundering, across flight/calendar/email/account domains) and `scripts/evaluate.py` to get a real precision/recall/FPR number instead of trusting "it works on the demo scenarios." Also built a TF-IDF cosine-similarity backend as the planned upgrade over plain lexical overlap, fixed two real bugs in it (missing stemming, a stemmed-vs-unstemmed stop-word mismatch), and then measured it losing to the simpler lexical backend anyway (0.8 vs 1.0 precision). Shipped lexical as the default based on that number, not on which one sounds more sophisticated. Full writeup in `docs/EVALUATION.md`. `sentence-transformers` remains the actual planned upgrade, still blocked by the torch install constraint noted below and in `aui/fidelity/backend.py`.

**Day 5 - forensic report -> presentable artifact**
Turn the plain-text forensic report into something screenshot-worthy (HTML export or a Streamlit tab), and write the 5-10 minute demo script (chat phase 8): normal delegation, then the two laundering scenarios, then the forensic reconstruction, then a direct side-by-side against "here's what a normal IAM audit log would have shown for the same sequence" (which is just "action permitted," nothing more). Risk: low, mostly writing and formatting.

**Day 6 - documentation + publishability pass (chat phase 10)**
Write up the design review, architecture, and threat model into a short paper-shaped writeup (even 4-6 pages), do the literature check on in-toto/SLSA/verifiable-credentials/confused-deputy/agent-safety literature that was flagged as owed back in the design review, and identify what a real evaluation would need (a labeled dataset of benign vs. laundering delegation chains, false-positive rate on realistic task decomposition, comparison against a pairwise-only baseline to show the transitive check earns its keep). Risk: low effort, but this is the part that turns "cool demo" into "could go further."

**Day 7 - buffer**
Rehearsal, fixing whatever breaks when you run the demo on a different machine than the one you built it on, README polish.

## Known v2 items, deliberately not in scope now

Graph DB (Neo4j) for branching/merging delegation graphs at real scale. Transparency-log anchoring so the Broker itself can't quietly rewrite history undetected. Hardware-backed agent identity / mTLS instead of self-registered keys. Real orchestration framework integration (LangGraph/CrewAI) instead of the hand-rolled harness. A real embedding/NLI similarity backend, once `sentence-transformers`/`torch` can install somewhere that isn't this authoring sandbox. A benchmark bigger than 18 hand-authored cases, ideally with an independent labeler instead of self-graded labels (see limitations in `docs/EVALUATION.md`).
