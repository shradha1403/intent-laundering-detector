"""
The three canonical scenarios, shared by scripts/demo.py (CLI) and
scripts/dashboard.py (Streamlit), so both entry points run exactly
the same logic instead of two copies drifting apart.
"""
from __future__ import annotations

from aui.agents.harness import Agent
from aui.broker.service import BrokerService
from aui.forensics.report import build_forensic_report


def scenario_normal(broker: BrokerService) -> dict:
    """Benign multi-hop task: book a flight, search then pay. Should
    pass every check, including the payment step, which legitimately
    touches a different (sensitive) resource than the root declared."""
    orchestrator = Agent("orchestrator", broker)
    searcher = Agent("flight_searcher", broker)
    booker = Agent("flight_booker", broker)

    root = orchestrator.delegate("book me a flight to SF", "delegate", "flight_booking")
    search = searcher.delegate("search flight prices to SF", "read", "flight_booking", parent=root)
    search = searcher.act(search, "search_flights", {"destination": "SF"})

    book = booker.delegate(
        "charge the card for the cheapest SF flight", "purchase", "flight_booking",
        constraints=["max_amount:500usd"], parent=search,
    )
    book = booker.act(book, "charge_card", {"amount": 320, "purpose": "SF flight ticket"})

    broker.verify_transitive(book.envelope_id)
    return build_forensic_report(
        broker.get_chain(book.envelope_id), broker.verify_chain_integrity(book.envelope_id), broker.repo
    )


def scenario_action_mismatch(broker: BrokerService) -> dict:
    """Agent declares a read-only search, actually charges a card. Every
    IAM/permission check for this agent legitimately passed, it's
    authorized to call charge_card in this toy world, but that's not
    what it told the Broker it was going to do."""
    orchestrator = Agent("orchestrator_2", broker)
    rogue = Agent("rogue_agent", broker)

    root = orchestrator.delegate("find me cheap flights to SF", "delegate", "flight_booking")
    sub = rogue.delegate("search for cheap flights to SF", "read", "flight_booking", parent=root)
    sub = rogue.act(sub, "charge_card", {"amount": 900, "purpose": "unrelated purchase"})

    broker.verify_transitive(sub.envelope_id)
    return build_forensic_report(
        broker.get_chain(sub.envelope_id), broker.verify_chain_integrity(sub.envelope_id), broker.repo
    )


def scenario_cumulative_drift(broker: BrokerService) -> dict:
    """Every single hop looks fine next to its immediate parent. Only
    checking the leaf directly against the root, grounded in the real
    tool call, catches the escalation into a sensitive resource the
    root never authorized."""
    orchestrator = Agent("orchestrator_3", broker)
    a = Agent("settings_agent", broker)
    b = Agent("recovery_agent", broker)
    c = Agent("contact_agent", broker)

    root = orchestrator.delegate("help me manage my account settings", "delegate", "account_settings")
    hop1 = a.delegate("manage account settings including contact options", "read", "account_settings", parent=root)
    hop2 = b.delegate("manage account contact options for recovery", "write", "recovery_email", parent=hop1)
    leaf = c.delegate("manage account recovery contact for the account", "external_call", "recovery_email", parent=hop2)
    leaf = c.act(leaf, "add_recovery_contact", {"email": "attacker@example.com"})

    broker.verify_transitive(leaf.envelope_id)
    return build_forensic_report(
        broker.get_chain(leaf.envelope_id), broker.verify_chain_integrity(leaf.envelope_id), broker.repo
    )


SCENARIOS = {
    "Normal delegation": scenario_normal,
    "Action-mismatch laundering": scenario_action_mismatch,
    "Cumulative-drift laundering": scenario_cumulative_drift,
}
