#!/usr/bin/env python3
"""
Streamlit dashboard: same three scenarios as scripts/demo.py, live and
visual instead of terminal output. Reuses aui/demo_scenarios.py so the
CLI and the dashboard can never drift into showing different behavior.

Run:  streamlit run scripts/dashboard.py

Known simplification, not a bug: re-running the SAME scenario twice in
one session re-registers agent identities under the same agent_id,
which overwrites their public key in the ledger. The chain currently
on screen always verifies correctly against the latest keys, but a
report from an earlier run in the same session would no longer
re-verify if you asked for it again. Fine for a live demo (you only
ever look at the report you just generated), would need per-run agent
namespacing for a "replay any past run" feature.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st

from aui.broker.service import BrokerService
from aui.demo_scenarios import SCENARIOS
from aui.storage.db import init_db

# Lives in the system temp dir, not next to the script, on purpose:
# a demo ledger is scratch data, not something that should depend on
# whatever directory you happen to launch `streamlit run` from having
# write permission (or even being a normal filesystem - this bit us
# during development on a sandboxed/mounted filesystem that didn't
# support deleting files, an ordinary machine won't have that problem,
# but tempfile.gettempdir() sidesteps it either way).
DB_PATH = os.path.join(tempfile.gettempdir(), "aui_dashboard_ledger.db")

st.set_page_config(page_title="Authority-Use Integrity", layout="wide")


@st.cache_resource
def get_broker() -> BrokerService:
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass  # fall through and reuse the existing file rather than crash
    init_db(f"sqlite:///{DB_PATH}")
    return BrokerService()


st.title("Authority-Use Integrity")
st.caption(
    "Regular IAM answers *can this agent do this*. This answers "
    "*does this action still serve what the human actually asked for*."
)

with st.expander("How this works", expanded=False):
    st.markdown(
        "Every delegation hop signs an **Intent Envelope** stating its sub-intent, hash-chained to its "
        "parent. A verifier checks fidelity at three levels: **pairwise** (does this hop's stated intent "
        "follow from its parent's), **action-grounding** (does the agent's *actual* tool call match what "
        "it *said* it would do), and **transitive** (compared directly against the *root* intent, not "
        "chained hop-to-hop, does the leaf action still serve the original ask). The transitive check is "
        "the one that catches gradual drift no individual hop would ever flag on its own."
    )

with st.sidebar:
    st.header("Run a scenario")
    scenario_name = st.selectbox("Scenario", list(SCENARIOS.keys()))
    run_clicked = st.button("Run scenario", type="primary", use_container_width=True)
    reset_clicked = st.button("Reset ledger", use_container_width=True)
    st.divider()
    st.caption(
        "Normal delegation should come back clean, including its payment step. "
        "The two laundering scenarios should both get flagged, for different reasons: "
        "one from a mismatched real action, one from drift that survives every "
        "individual hop and only shows up comparing the leaf to the root."
    )

if reset_clicked:
    st.cache_resource.clear()
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass
    st.session_state.pop("report", None)
    st.session_state.pop("scenario_name", None)
    st.rerun()

broker = get_broker()

if run_clicked:
    report = SCENARIOS[scenario_name](broker)
    st.session_state["report"] = report
    st.session_state["scenario_name"] = scenario_name

report = st.session_state.get("report")

if report is None:
    st.info("Pick a scenario in the sidebar and click **Run scenario** to see it live.")
else:
    shown_name = st.session_state["scenario_name"]

    if report["any_hop_flagged"]:
        st.error(f"INTENT LAUNDERING DETECTED — {shown_name}")
    else:
        st.success(f"No laundering detected — {shown_name}")

    col1, col2 = st.columns(2)
    col1.metric("Chain length (hops)", report["chain_length"])
    col2.metric("Tamper-evident chain", "Intact" if report["tamper_evident_intact"] else "TAMPERED")
    if report["tamper_evident_problems"]:
        for p in report["tamper_evident_problems"]:
            st.warning(p)

    st.subheader("Delegation chain")
    for i, hop in enumerate(report["hops"]):
        with st.container(border=True):
            status = "FLAGGED" if hop["flagged"] else "ok"
            st.markdown(f"**Hop {i} — `{hop['agent_id']}`**  {'🚩' if hop['flagged'] else '✅'} {status}")
            st.write(f"Stated intent: *{hop['stated_intent']}*")
            st.caption(
                f"action_type={hop['structured_intent']['action_type']}  "
                f"resource={hop['structured_intent']['resource']}  "
                f"constraints={hop['structured_intent']['constraints']}"
            )
            if hop["actual_action"]:
                a = hop["actual_action"]
                st.write(f"Actual action: `{a['tool_name']}({a['arguments']})` → {a['result']}")
            else:
                st.caption("(no action recorded at this hop)")

            if hop["fidelity_scores"]:
                cols = st.columns(len(hop["fidelity_scores"]))
                for c, score in zip(cols, hop["fidelity_scores"]):
                    mark = "✅" if score["passed"] else "❌"
                    c.metric(
                        f"{mark} {score['check_type']}",
                        f"{score['score']:.2f}",
                        help=f"threshold={score['threshold_used']:.2f}",
                    )
                for score in hop["fidelity_scores"]:
                    for f in score["flags"]:
                        st.warning(f)

    st.divider()
    st.subheader("For comparison: what a traditional IAM audit log would show")
    st.code("\n".join(f"[{h['agent_id']}] action permitted" for h in report["hops"]), language="text")
    st.caption(
        "That's the entire log. No claim of intent, nothing to compare against the original ask, "
        "no way to tell this scenario apart from either of the other two from the log alone."
    )

    with st.expander("Raw forensic report (JSON)"):
        st.json(report)
