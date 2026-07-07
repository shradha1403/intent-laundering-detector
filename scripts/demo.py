#!/usr/bin/env python3
"""
Live demo script: three scenarios in one run, printed as forensic
reports. See aui/demo_scenarios.py for the actual scenario logic,
shared with scripts/dashboard.py so both entry points run identically.

Run:  python scripts/demo.py
"""
from __future__ import annotations

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aui.storage.db import init_db
from aui.broker.service import BrokerService
from aui.forensics.report import render_report_text
from aui.demo_scenarios import scenario_normal, scenario_action_mismatch, scenario_cumulative_drift


def main():
    # Scratch data lives in the system temp dir rather than next to the
    # script, same reasoning as scripts/dashboard.py: don't assume the
    # launch directory is writable or even a normal filesystem.
    db_path = os.path.join(tempfile.gettempdir(), "aui_demo_ledger.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    init_db(f"sqlite:///{db_path}")
    broker = BrokerService()

    print("\n" + "#" * 72)
    print("# SCENARIO 1: NORMAL DELEGATION (benign multi-hop task)")
    print("#" * 72)
    r1 = scenario_normal(broker)
    print(render_report_text(r1))

    print("\n" + "#" * 72)
    print("# SCENARIO 2: INTENT LAUNDERING - action mismatch")
    print("# Agent declares a read-only search, actually charges a card.")
    print("#" * 72)
    r2 = scenario_action_mismatch(broker)
    print(render_report_text(r2))

    print("\n" + "#" * 72)
    print("# SCENARIO 3: INTENT LAUNDERING - cumulative drift")
    print("# Every single hop looks fine next to its immediate parent.")
    print("# Only checking root directly against the leaf catches it.")
    print("#" * 72)
    r3 = scenario_cumulative_drift(broker)
    print(render_report_text(r3))

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Scenario 1 (normal)            flagged={r1['any_hop_flagged']}   (expected: False)")
    print(f"Scenario 2 (action mismatch)   flagged={r2['any_hop_flagged']}   (expected: True)")
    print(f"Scenario 3 (cumulative drift)  flagged={r3['any_hop_flagged']}   (expected: True)")

    ok = (not r1["any_hop_flagged"]) and r2["any_hop_flagged"] and r3["any_hop_flagged"]
    print("\nDEMO RESULT:", "PASS - system distinguishes normal delegation from both laundering patterns" if ok else "FAIL - check thresholds/scenarios")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
