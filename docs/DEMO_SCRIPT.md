# Demo Script

Target: 7-8 minutes, leaves 2 minutes of slack for questions in a 10-minute slot. Runs entirely from `streamlit run scripts/dashboard.py`, one browser tab, no slides needed, though a single title slide with the tagline is fine if the format expects one.

## 0:00-1:00 - The problem, in one sentence

Say it plainly, don't read it: "Every authorization system today answers one question: can this agent do this. My project answers a different question: does this action still serve what the human actually asked for. Those are not the same question, and the gap between them is where an agent can hold every permission it needs and still betray the original ask."

Open the dashboard. Point at the tagline under the title. Don't click anything yet.

## 1:00-2:30 - Scenario 1: normal delegation

Select "Normal delegation," click Run. Walk through the three hop cards as they render: orchestrator asks for a flight, a search agent looks up prices, a booking agent charges the card. Point out the green checkmarks and the success banner. Say explicitly: "Notice the payment step touches a different, more sensitive resource than the root request ever mentioned, and it's still not flagged, because paying for a flight you asked to book is exactly what should happen. A system that flags this is a system nobody will trust in a week." This is the moment that pre-empts the "isn't this just going to cry wolf constantly" objection before anyone asks it.

## 2:30-4:00 - Scenario 2: action mismatch

Select "Action-mismatch laundering," click Run. Point at the second hop: it declared "search for cheap flights," its real tool call was `charge_card` for $900. Say: "Every permission check here passed. This agent is authorized to call charge_card in this system. The only thing that catches this is comparing what it said against what it actually did, and that comparison is the part most designs like this skip. Text-similarity-only defenses miss this entirely, because there's no text to compare, the mismatch is between the claim and the receipt." Point at the red banner and the specific `action_mismatch` flag.

## 4:00-6:00 - Scenario 3: cumulative drift (the centerpiece)

Select "Cumulative-drift laundering," click Run. This is the one to slow down for. Walk hop by hop: "manage my account settings" to "review contact options" to "update recovery contact info" to the actual action, adding an attacker's email as the recovery contact. Say: "Every single one of those hops, checked against the one before it, looks completely reasonable. If you only ever compare a step to its immediate parent, this sails through clean. The only thing that catches it is comparing the final action directly against the ORIGINAL request, not the most recent one." Point at the `resource_escalation` flag and say plainly that this is the actual research claim: local, hop-to-hop fidelity checks can all pass while the chain as a whole launders intent.

## 6:00-6:45 - Forensic reconstruction and the IAM comparison

Scroll to the bottom panel. Point at the plain-text IAM comparison: "Here's what a normal audit log shows for this exact sequence: four lines that all say 'action permitted.' No claim of intent anywhere in it, no way to tell this apart from the benign scenario you just watched pass. That's not a strawman, that's literally what most production IAM logging looks like today." Then point at the full forensic report expander: signed, hash-chained, tamper-evident, shows exactly which hop and which claim broke down.

## 6:45-7:30 - Honesty checkpoint (do this even if nobody asks)

Say this without being asked, it's better coming from you first: "I did a literature check on this before finishing it. In the last few months, several research teams independently converged on almost exactly this problem, and one paper in particular, SentinelAgent, published in April, does a more rigorous version of this exact mechanism with formal proofs and a 516-scenario benchmark. I'm citing it because pretending to have invented something that a half-dozen teams built around the same time isn't honest, and because the fact that independent teams converged on the same architecture is itself a signal the problem was framed correctly. What's mine here is a working, from-scratch, open-source implementation with its own red-team findings, including two real bugs I found and fixed by attacking my own system before this demo, not a claim to have been first."

This preempts the single most damaging question a technically literate judge could ask, and answering it yourself, unprompted, reads as competence rather than as a weakness. See `docs/PUBLISHABILITY.md` for the full literature comparison if someone wants the details.

## 7:30-8:00 - Close

"Regular IAM tells you what was permitted. This tells you what was still faithful to the ask. As agents get more autonomy, the second question matters more than the first." Stop there. Don't add more, the honesty checkpoint already did the heavy lifting on credibility.

## Anticipated questions and honest answers

**"How is this different from just tighter permission scopes?"** Scopes answer whether an action was allowed. This answers whether an allowed action still served the original request. Scenario 2 is authorized the whole way through and still wrong.

**"Doesn't this just move the problem to whether your similarity model is any good?"** Yes, and say so directly. The current similarity backend is a lexical stand-in (difflib plus token overlap), not a real embedding or NLI model, documented as such in the README. SentinelAgent's own paper proves, via a Rice's-theorem argument and eight deliberately ambiguous test cases, that deterministic intent verification is impossible in principle, so a probabilistic, sometimes-wrong signal feeding a policy decision is the honest state of the art here, not a flaw unique to this project.

**"What stops the Broker itself from being compromised?"** Nothing in the current MVP. It's a single trusted service, documented as a known limitation. A real deployment would want a transparency-log pattern, publishing Merkle roots externally so even the Broker can't quietly rewrite history undetected.

**"Does this handle an agent that fans out to multiple children and merges results?"** No, and that's a documented, tested limitation (`test_fan_out_fan_in_not_modeled`), not a silent gap. The chain model assumes one parent per envelope.

**"What would it take to publish this?"** Point to `docs/PUBLISHABILITY.md`. Short answer: the narrow mechanism isn't novel enough anymore as of this literature check, but a systematic comparison across the half-dozen concurrent frameworks, or an independent adversarial replication of one of their published results, both are legitimate paths, and neither has been done yet by anyone outside the original authors.
