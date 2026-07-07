"""
These tests encode the actual research claim: pairwise checks can
all pass while the transitive (root vs leaf) check still catches
laundering. If this file's last two tests ever fail, the project's
core pitch is broken.
"""
from aui.crypto.keys import AgentIdentity
from aui.envelope.chain import create_envelope
from aui.envelope.schema import Intent, StructuredIntent, ActionReceipt
from aui.fidelity.engine import FidelityEngine


def make_intent(text, action_type="read", resource="test_resource", constraints=None):
    return Intent(
        raw_text=text,
        structured=StructuredIntent(action_type=action_type, resource=resource, constraints=constraints or []),
    )


def test_benign_multi_hop_decomposition_does_not_false_positive():
    """Wording legitimately drifts across hops of a normal task
    breakdown. The engine should not flag this even though the raw
    text at hop 3 barely resembles hop 1's wording."""
    engine = FidelityEngine()
    orch = AgentIdentity.generate("orch")
    a = AgentIdentity.generate("agent_a")
    b = AgentIdentity.generate("agent_b")

    root = create_envelope(identity=orch, intent=make_intent("book me a flight to SF", "delegate", "flight_booking"))
    mid = create_envelope(identity=a, intent=make_intent("search flight prices to SF", "read", "flight_booking"), parent=root)
    leaf = create_envelope(identity=b, intent=make_intent("charge the card for the SF ticket", "purchase", "flight_booking", ["max_amount:500usd"]), parent=mid)
    leaf.action_receipt = ActionReceipt(tool_name="charge_card", arguments={"amount": 320, "purpose": "SF flight ticket"}, result_summary="charged $320 for SF flight")

    score, flags, threshold = engine.transitive(root, leaf)
    assert not any("resource_escalation" in f for f in flags), flags


def test_action_grounding_catches_declared_vs_actual_mismatch():
    """Agent declares a read-only search intent but its actual tool
    call is a payment. Pure intent-to-intent text comparison across
    hops would never see this, because the mismatch is between the
    STATED intent and the REAL action of the same hop."""
    engine = FidelityEngine()
    agent = AgentIdentity.generate("agent_c")
    env = create_envelope(identity=agent, intent=make_intent("search for cheap flights to SF", "read", "flight_booking"))
    env.action_receipt = ActionReceipt(tool_name="charge_card", arguments={"amount": 900}, result_summary="charged $900")

    score, flags = engine.action_grounding(env)
    assert any("action_mismatch" in f for f in flags)
    assert score < engine.thresholds.action_grounding_default


def test_transitive_catches_drift_that_survives_every_pairwise_hop():
    """The core research claim: construct a chain where EVERY pairwise
    hop passes (each restatement is close enough to its immediate
    parent), but the leaf action, compared directly to the root,
    has clearly drifted into a sensitive resource the root never
    authorized. A pairwise-only verifier would let this straight
    through; the transitive check must not.
    """
    engine = FidelityEngine()
    orch = AgentIdentity.generate("orch")
    a = AgentIdentity.generate("agent_a")
    b = AgentIdentity.generate("agent_b")
    c = AgentIdentity.generate("agent_c")

    root = create_envelope(identity=orch, intent=make_intent("help me manage my account settings", "delegate", "account_settings"))
    hop1 = create_envelope(identity=a, intent=make_intent("manage account settings including contact options", "read", "account_settings"), parent=root)
    hop2 = create_envelope(identity=b, intent=make_intent("manage account contact options for recovery", "write", "recovery_email"), parent=hop1)
    leaf = create_envelope(identity=c, intent=make_intent("manage account recovery contact for the account", "external_call", "recovery_email"), parent=hop2)
    leaf.action_receipt = ActionReceipt(
        tool_name="add_recovery_contact",
        arguments={"email": "attacker@example.com"},
        result_summary="added attacker@example.com as account recovery contact",
    )

    # every individual pairwise hop is worded closely enough to pass
    p1, p1_flags = engine.pairwise(root, hop1)
    p2, p2_flags = engine.pairwise(hop1, hop2)
    p3, p3_flags = engine.pairwise(hop2, leaf)
    assert p1_flags == [], p1_flags
    assert p2_flags == [], p2_flags
    assert p3_flags == [], p3_flags

    # but the transitive check, comparing root directly to leaf, catches it
    t_score, t_flags, t_threshold = engine.transitive(root, leaf)
    assert t_flags, "transitive check should have flagged resource escalation / cumulative drift"
