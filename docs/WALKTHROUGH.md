# Authority-Use Integrity: catching AI agents that launder intent, stage by stage

I just finished my capstone for The Cyber Instructor's AI for Cybersecurity bootcamp, and I want to walk through what it actually does, because "intent laundering detection" doesn't mean anything until you see the failure it's built to catch.

## The one-line problem

Regular IAM answers "can this agent do this." Nobody's answering "does this action still serve what the human actually asked for."

That gap matters more now because AI agents don't just run one task anymore, they delegate. One agent asks another agent to help, which asks another, and so on. Every hop in that chain checks permissions like normal, and every single check can come back green, while the thing the chain ends up doing has quietly drifted away from what the human at the top actually wanted. That's intent laundering: an agent using authority it legitimately holds, for a purpose that no longer matches the original ask, without ever tripping a permission error.

## Where this sits in the field, said up front

Before going further: this exact idea is not undiscovered. A literature check I did after building the MVP turned up several teams that converged on close to the same architecture earlier this year. SentinelAgent (arXiv:2604.02767) is the closest hit, a formal delegation-chain framework with a proven fidelity property and a pre/at/post-execution verification lifecycle shaped almost exactly like mine. HDP (arXiv:2604.04522) already standardizes the signed provenance layer I built as an IETF draft with a shipping SDK. Authorization Propagation in Multi-Agent AI Systems (arXiv:2605.05440) and Agent Drift (arXiv:2601.04170) cover overlapping ground too.

So what I'm showing here is an independent, from-scratch build of that idea, with my own red-team findings, not a claim to have gotten there first. If you already know that literature, you'll recognize the shape immediately. If you don't, this is a decent on-ramp into it.

## Stage 1: every hop signs what it claims it's doing

The core data structure is an Intent Envelope. Every time one agent delegates to another, that hop creates a signed record: what it claims it's doing, in plain text and in a structured action/resource/constraints form, signed with Ed25519, hashed with a canonical JSON hash, and chained to its parent's hash. Tamper with any hop and every hash after it breaks. This part borrows directly from supply-chain attestation patterns like in-toto, I'm not pretending the crypto layer is novel.

One detail I got wrong the first time and had to fix: the signature originally covered the action receipt too, but the receipt only exists after the action runs, which is after the signature. Every post-action verification failed because I was trying to verify a signature against data that didn't exist yet when it was created. Fixed by excluding the receipt from what gets signed, since a signature is a claim about intent at the moment it was made, and the receipt is a separately captured fact recorded later.

## Stage 2: three checks, and the third one is the actual point

Every envelope gets checked at three levels.

Pairwise asks whether this hop's stated intent plausibly follows from its parent's. That's the easy one, basically comparing two pieces of text.

Action-grounding asks whether the agent's actual tool call matches what it said it would do. This is the part a pure text-similarity system skips entirely: it's not comparing two paraphrases, it's comparing a claim against a real, executed action.

Transitive asks the question that actually matters: compared directly against the root intent, not chained pairwise score to pairwise score, does the leaf action still serve the original request? This is the one built specifically to catch a "boiling frog" pattern, where a chain starts at "book me a flight," passes through calendar checks and payment lookups that each look totally reasonable next to their immediate parent, and ends up adding a new recovery email to the account. Every individual hop passes. Only comparing the very first request against the very last action exposes that the chain wandered somewhere nobody authorized.

Worth being exact about how, since an independent audit called out that earlier drafts of this writeup made it sound more sophisticated than it is: what actually catches that recovery-email example isn't a semantic model deciding the wording drifted. It's a categorical rule, grounded in the real tool call rather than what the agent claims about itself, that asks "is this a sensitive resource the root task never expected to touch." If yes, flagged, regardless of how innocuous the wording sounds. The text-similarity score between root and leaf is a secondary signal riding along on top of that, not the thing doing the actual catching. So: a real, working control, just a simpler and more brittle one than "semantic drift detection" implies. It generalizes only as far as the hand-authored list of expected sensitive sub-resources per task type, which is itself a disclosed limitation, not a hidden one, see `docs/AUDIT.md`.

## Stage 3: how it's wired together

Agents don't get direct access to real tools. Every action goes through an interceptor, which is the only path any agent has to actually do something, and the interceptor reports to a broker (a framework-agnostic service with a thin FastAPI layer on top). The broker runs the three-stage check, writes everything to a hash-chained ledger in SQLite, and a Streamlit dashboard shows the whole chain hop by hop, with a laundering banner when something gets flagged and a forensic report you can read like an incident writeup. Nothing in the pipeline calls a cloud model, so a live demo doesn't depend on internet access working in the room.

## Stage 4: red-teaming it myself, and what it actually found

Before building the dashboard, I spent time trying to fool my own verifier instead of just trusting that it worked. Two real bugs came out of that, both found by actually running constructed attacks rather than reasoning about them on paper.

First, the transitive check was trusting an agent's own self-declared resource field instead of what its tool call actually touched. An attacker could claim it was working with something harmless while its real action hit something sensitive, and the check never noticed because it was checking a claim, not a fact. Fixed by grounding the check in the tool that was actually called.

That fix immediately caused a new problem: a completely normal flight purchase started getting flagged as an escalation, because paying for a flight touches a payment resource that's sensitive by category, even though that's a totally expected part of booking a flight. Fixed with an explicit allowlist of which sensitive sub-actions are a normal consequence of a given root task, where anything not on that list still defaults to getting flagged, since that's the safer failure direction for a security control.

## Stage 5: building a benchmark, and shipping the "boring" option on purpose

Three demo scenarios passing isn't evidence of anything beyond "it works on the three things I wrote." So I built an 18-case labeled benchmark across four domains (flight, calendar, email, account/recovery), split between benign and laundering chains, and a script that computes real precision, recall, false-positive rate, and accuracy instead of eyeballing pass/fail.

I also tried to upgrade the similarity backend from plain lexical word overlap to TF-IDF cosine similarity, which sounds like a strict improvement. It wasn't. I found and fixed two real bugs while building it (missing stemming, so "flight" and "flights" shared zero vocabulary, and a stop-word list that stopped matching once I added stemming), and after both fixes, it still scored worse than the simple lexical baseline on this benchmark: 0.80 precision versus 1.00, a 0.20 false-positive rate versus 0.00. So I shipped the lexical backend as the default anyway. The whole investigation is written up in `docs/EVALUATION.md`, including the bugs that turned out to be dead ends, because the point of running a benchmark is finding out you're wrong sometimes, not confirming what you already assumed.

## What this doesn't do yet

It's a working proof of the detection idea, not a deployable product. The eight tools it uses (search flights, check calendar, send email, and so on) are hand-written stand-ins, not real hooks into an actual agent framework, so plugging this into a real multi-agent system would mean rewriting the interceptor to sit in front of whatever real tool calls that system makes. And the chain model only handles a straight line of delegation, one parent per hop, with no way to represent an agent that forks work into parallel branches and merges the results back together. All of that was written down honestly in the repo from the start.

An independent adversarial audit went further than that self-assessment and found things the docs hadn't disclosed: the API had zero authentication on any route (anyone who could reach the port could write to the ledger), and the broker kept agent signing keys in memory only, so a restart made every previously-registered agent unable to sign anything new. Both got fixed, with tests proving it (`docs/AUDIT.md` has the full writeup, the diffs, and the before/after verification). What's still true and still open: agent identity is self-registered, not attested - the API now requires a shared token to call `register_agent` at all, which raises the bar from "zero effort" to "you need the broker's credential," but it does not stop someone holding that credential from minting an identity under any name they like. Real attestation needs hardware-backed keys or mTLS client certs, which is genuinely out of scope for something that runs on a laptop, not a gap that was just easier to leave unfixed.

## Try it

```
git clone <repo-url>
pip install -r requirements.txt
pytest -v                          # 35 tests: crypto, chain integrity, fidelity engine, red-team cases,
                                    # the FastAPI layer, broker-restart survival, concurrency, tool-map consistency
python scripts/demo.py             # normal delegation + two laundering patterns, end to end
python scripts/evaluate.py         # the 18-case benchmark, both similarity backends
streamlit run scripts/dashboard.py # the same three scenarios, live and visual
```

Full writeups on the architecture decisions, the red-team findings, the benchmark investigation, and an honest gap analysis against the closest prior art are in the repo's `docs/` folder.
