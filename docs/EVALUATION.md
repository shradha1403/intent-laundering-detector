# Evaluation

## What this is

An 18-case labeled benchmark (`aui/benchmark.py`, run via `scripts/evaluate.py`), 10 benign delegation chains and 8 laundering chains across 4 domains (flight booking, calendar, email, account/recovery settings). Built specifically to get an honest, measured number instead of relying on the fact that the 3 demo scenarios and 5 hand-picked adversarial tests happen to pass. This is not DelegationBench-scale (516 scenarios, see `docs/PUBLISHABILITY.md`), it's a small, hand-authored check, and it should be read as exactly that.

## The result nobody was expecting going in

The plan was: swap the lexical similarity backend (difflib + word overlap) for something better, get better numbers, done. That is not what happened.

| metric | LexicalSimilarityBackend | TfidfSimilarityBackend |
|---|---|---|
| precision | 1.000 | 0.800 |
| recall | 1.000 | 1.000 |
| false positive rate | 0.000 | 0.200 |
| accuracy | 1.000 | 0.889 |

The simpler backend won. On this benchmark, at least.

## What actually happened building the "upgrade"

A real embedding model (`sentence-transformers`) was attempted first and specifically blocked: it needs `torch`, and installing `torch` did not complete within the authoring environment's execution limits, with no way to run it as a background job across tool calls. Not a time-management excuse, a verified, specific constraint (see `aui/fidelity/backend.py::EmbeddingSimilarityBackend`).

The fallback was TF-IDF cosine similarity (scikit-learn), fit on a small fixed reference corpus, blended with the existing lexical score. Three real bugs were found and fixed while building it, in order:

1. **Not a bug, a wrong hypothesis.** The first false positives looked like they might be caused by action descriptions being formatted like Python dicts (`search_flights {'destination': 'Denver'} found 3 flights...`) instead of sentences. Rewrote `describe_action()` to produce plain, sentence-like text. Re-ran the benchmark. Zero change in the numbers. The hypothesis was wrong, and the benchmark caught that immediately instead of it going unnoticed.
2. **The real bug.** Direct inspection of the cosine similarity computation showed a flat `0.0` between `"check flight prices to Denver"` and a description containing `"flights"`. TfidfVectorizer does exact string matching per token: "flight" and "flights" are different vocabulary entries. Added a Porter stemmer (`nltk`) as the tokenizer's preprocessing step. This fixed 1 of 3 false positives.
3. **A second real bug, surfaced by sklearn's own warning.** Stemming every token before vectorizing broke stop-word filtering, since sklearn's built-in stop-word list is unstemmed ("above") and no longer matched the now-stemmed tokens ("abov"). Stemmed the stop-word list too. This fixed a crash (sklearn also requires `stop_words` to be a list, not a set, a second small bug caught by the traceback) and cleaned up the warning, but did not move the benchmark numbers further.

After all three fixes: TF-IDF went from 0.727 to 0.800 precision. Still worse than the lexical baseline's 1.000.

## The decision

`FidelityEngine`'s default backend is `LexicalSimilarityBackend`, not `TfidfSimilarityBackend`. That's a deliberate choice made from this data, not an oversight. `TfidfSimilarityBackend` remains in the codebase, documented, available to instantiate directly, and this file exists so nobody has to rediscover this the hard way. `scripts/evaluate.py` can be re-run any time the fidelity engine changes, to check whether that's still the right call.

## Why the wider benchmark still mattered even though the "upgrade" lost

Because the three-scenario demo alone would never have caught this. All three original scenarios still pass under either backend. It took a benchmark with more benign cases, spanning more domains, to expose that a fixed, small reference corpus doesn't generalize well to sentence patterns it wasn't built from. That's the actual point of building a benchmark instead of trusting a demo: a demo shows you the cases you already thought to check, a benchmark has a chance of showing you the ones you didn't.

## Honest limitations of this benchmark itself

18 cases is small. All cases were hand-authored by the same person who built the system being tested, which is exactly the kind of self-grading a real evaluation should avoid, flagged here rather than hidden. No inter-rater agreement on the "expected_laundering" labels, since there's only one rater. Extending this into something more rigorous (more cases, an independent labeler, comparison against a published benchmark like DelegationBench if it becomes available) is real future work, not implied to be done here.
