#!/usr/bin/env python3
"""
Run the labeled benchmark in aui/benchmark.py and report real
precision/recall/FPR/FNR, for both similarity backends, instead of
just showing three scenarios and calling the system validated.

Run:  python scripts/evaluate.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aui.benchmark import CASES, BenchmarkCase
from aui.broker.service import BrokerService
from aui.fidelity.engine import FidelityEngine
from aui.fidelity.backend import SimilarityBackend, LexicalSimilarityBackend, TfidfSimilarityBackend
from aui.storage.db import init_db


def run_case(broker: BrokerService, case: BenchmarkCase) -> bool:
    """Returns whether the system flagged this chain as laundering."""
    parent = None
    leaf = None
    for hop in case.hops:
        intent_kwargs = dict(
            raw_text=hop.raw_text,
            action_type=hop.action_type,
            resource=hop.resource,
            constraints=hop.constraints,
        )
        from aui.envelope.schema import Intent, StructuredIntent

        intent = Intent(raw_text=hop.raw_text, structured=StructuredIntent(
            action_type=hop.action_type, resource=hop.resource, constraints=hop.constraints
        ))
        broker.register_agent(hop.agent_id)
        env = broker.create_envelope(hop.agent_id, intent, parent.envelope_id if parent else None)
        if hop.tool_name:
            env = broker.execute_action(env.envelope_id, hop.tool_name, hop.arguments)
        parent = env
        leaf = env

    broker.verify_transitive(leaf.envelope_id)
    from aui.forensics.report import build_forensic_report

    chain = broker.get_chain(leaf.envelope_id)
    report = build_forensic_report(chain, broker.verify_chain_integrity(leaf.envelope_id), broker.repo)
    return report["any_hop_flagged"]


def evaluate(backend: SimilarityBackend, db_path: str) -> dict:
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(f"sqlite:///{db_path}")
    broker = BrokerService(engine=FidelityEngine(backend=backend))

    tp = fp = tn = fn = 0
    rows = []
    for case in CASES:
        flagged = run_case(broker, case)
        expected = case.expected_laundering
        if expected and flagged:
            tp += 1
        elif expected and not flagged:
            fn += 1
        elif not expected and flagged:
            fp += 1
        else:
            tn += 1
        rows.append((case.name, case.domain, expected, flagged, "OK" if expected == flagged else "WRONG"))

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    accuracy = (tp + tn) / len(CASES)

    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall, "fpr": fpr, "accuracy": accuracy,
        "rows": rows,
    }


def print_results(name: str, results: dict):
    print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
    for case_name, domain, expected, flagged, status in results["rows"]:
        marker = " " if status == "OK" else " <-- MISCLASSIFIED"
        print(f"  [{domain:10s}] {case_name:38s} expected={expected!s:5s} got={flagged!s:5s}{marker}")
    print(f"\n  TP={results['tp']} FP={results['fp']} TN={results['tn']} FN={results['fn']}")
    print(f"  precision={results['precision']:.3f}  recall={results['recall']:.3f}  "
          f"FPR={results['fpr']:.3f}  accuracy={results['accuracy']:.3f}")


def main():
    n_laundering = sum(1 for c in CASES if c.expected_laundering)
    n_benign = len(CASES) - n_laundering
    print(f"Benchmark: {len(CASES)} cases ({n_benign} benign, {n_laundering} laundering) across "
          f"{len(set(c.domain for c in CASES))} domains")

    lexical_results = evaluate(LexicalSimilarityBackend(), "/tmp/aui_eval_lexical.db")
    print_results("LexicalSimilarityBackend (difflib + Jaccard)", lexical_results)

    tfidf_results = evaluate(TfidfSimilarityBackend(), "/tmp/aui_eval_tfidf.db")
    print_results("TfidfSimilarityBackend (TF-IDF cosine + lexical blend)", tfidf_results)

    print(f"\n{'=' * 72}\nSUMMARY\n{'=' * 72}")
    print(f"{'metric':12s} {'lexical':>10s} {'tfidf':>10s}")
    for key in ("precision", "recall", "fpr", "accuracy"):
        print(f"{key:12s} {lexical_results[key]:>10.3f} {tfidf_results[key]:>10.3f}")


if __name__ == "__main__":
    main()
