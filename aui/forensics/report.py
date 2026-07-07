"""
Forensic reconstruction: given a leaf envelope, walk the signed chain
back to root and produce a human-readable incident report.

This is the "receipts" moment of the whole project. A normal IAM
audit log tells you "action X was permitted." This tells you who
claimed what at each hop, what they actually did, whether the record
has been tampered with, and exactly where fidelity broke down, if it
did.
"""
from __future__ import annotations

from aui.envelope.schema import Envelope
from aui.storage.repository import EnvelopeRepository


def build_forensic_report(chain: list[Envelope], integrity_problems: list[str], repo: EnvelopeRepository) -> dict:
    hops = []
    for env in chain:
        scores = repo.get_fidelity_scores(env.envelope_id)
        hops.append(
            {
                "envelope_id": env.envelope_id,
                "agent_id": env.agent_id,
                "stated_intent": env.intent.raw_text,
                "structured_intent": env.intent.structured.model_dump(),
                "actual_action": (
                    {
                        "tool_name": env.action_receipt.tool_name,
                        "arguments": env.action_receipt.arguments,
                        "result": env.action_receipt.result_summary,
                    }
                    if env.action_receipt
                    else None
                ),
                "content_hash": env.provenance.content_hash,
                "signer": env.provenance.signer_pubkey_id,
                "fidelity_scores": scores,
                "flagged": any(not s["passed"] for s in scores),
            }
        )

    any_flagged = any(h["flagged"] for h in hops)
    first_flagged = next((h for h in hops if h["flagged"]), None)

    return {
        "root_envelope_id": chain[0].envelope_id if chain else None,
        "leaf_envelope_id": chain[-1].envelope_id if chain else None,
        "chain_length": len(chain),
        "tamper_evident_intact": integrity_problems == [],
        "tamper_evident_problems": integrity_problems,
        "any_hop_flagged": any_flagged,
        "first_flagged_hop": first_flagged,
        "hops": hops,
    }


def render_report_text(report: dict) -> str:
    """Plain-text rendering for CLI / demo output."""
    lines = []
    lines.append("=" * 72)
    lines.append("AUTHORITY-USE INTEGRITY - FORENSIC RECONSTRUCTION REPORT")
    lines.append("=" * 72)
    lines.append(f"Chain: {report['root_envelope_id']} -> ... -> {report['leaf_envelope_id']}")
    lines.append(f"Hops: {report['chain_length']}")
    lines.append(f"Tamper-evident chain intact: {report['tamper_evident_intact']}")
    if report["tamper_evident_problems"]:
        for p in report["tamper_evident_problems"]:
            lines.append(f"  ! {p}")
    lines.append(f"Intent laundering detected: {report['any_hop_flagged']}")
    lines.append("-" * 72)

    for i, hop in enumerate(report["hops"]):
        marker = "FLAGGED" if hop["flagged"] else "ok"
        lines.append(f"[hop {i}] agent={hop['agent_id']}  [{marker}]")
        lines.append(f"  stated intent : {hop['stated_intent']}")
        if hop["actual_action"]:
            lines.append(
                f"  actual action : {hop['actual_action']['tool_name']}({hop['actual_action']['arguments']})"
            )
            lines.append(f"                  -> {hop['actual_action']['result']}")
        else:
            lines.append("  actual action : (none recorded yet)")
        for score in hop["fidelity_scores"]:
            status = "PASS" if score["passed"] else "FAIL"
            lines.append(
                f"  [{status}] {score['check_type']}: score={score['score']:.2f} "
                f"(threshold={score['threshold_used']:.2f})"
            )
            for f in score["flags"]:
                lines.append(f"           -> {f}")
        lines.append("")

    lines.append("=" * 72)
    return "\n".join(lines)
