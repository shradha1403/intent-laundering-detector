"""
Red-team pass against the fidelity engine.

Each test here documents a specific way someone might try to fool the
verifier. Where a test found a real gap, the fix lives in
aui/fidelity/engine.py and the test now proves the fix holds. Where a
gap is real and NOT fixed (see test_fan_out_fan_in_not_modeled at the
bottom), the test documents that honestly instead of hiding it.
"""
from aui.crypto.keys import AgentIdentity
from aui.envelope.chain import create_envelope
from aui.envelope.schema import Intent, StructuredIntent, ActionReceipt
from aui.fidelity.engine import FidelityEngine


def make_intent(text, action_type, resource, constraints=None):
    return Intent(
        raw_text=text,
        structured=StructuredIntent(action_type=action_type, resource=resource, constraints=constraints or []),
    )


def test_resource_misdeclaration_is_caught():
    """Attack: declare a benign resource field while the real tool call
    touches a sensitive one. Found during red-teaming: the original
    transitive check trusted the agent's self-declared `resource`
    field for its escalation check, so this attack sailed through
    with zero flags. Fixed by grounding the check in the actual tool
    that was called (aui.fidelity.engine.actual_resource_touched),
    not what the agent claims about itself.
    """
    engine = FidelityEngine()
    orch = AgentIdentity.generate("orch")
    rogue = AgentIdentity.generate("rogue")

    root = create_envelope(identity=orch, intent=make_intent("help me manage my account", "delegate", "account_settings"))
    # declares an innocuous resource, action_type is broad enough to
    # legitimately cover the sensitive tool
    leaf = create_envelope(
        identity=rogue,
        intent=make_intent("contact support about my account", "external_call", "account_settings"),
        parent=root,
    )
    leaf.action_receipt = ActionReceipt(
        tool_name="add_recovery_contact",
        arguments={"email": "attacker@example.com"},
        result_summary="added attacker@example.com as account recovery contact",
    )

    action_score, action_flags = engine.action_grounding(leaf)
    transitive_score, transitive_flags, transitive_threshold = engine.transitive(root, leaf)

    assert any("resource_misdeclaration" in f for f in action_flags), action_flags
    assert any("resource_escalation" in f for f in transitive_flags), transitive_flags


def test_keyword_stuffed_restatement_does_not_bypass_action_grounding():
    """Attack: stuff the declared intent text with the parent's exact
    words to inflate lexical similarity, while calling a completely
    different tool. This is exactly the kind of attack a pure
    text-similarity verifier would miss. The rule-based half of
    action-grounding (is this tool even in the declared action_type's
    allowed set) doesn't care how convincing the text is.
    """
    engine = FidelityEngine()
    orch = AgentIdentity.generate("orch")
    rogue = AgentIdentity.generate("rogue")

    root = create_envelope(identity=orch, intent=make_intent("search for cheap flights to SF for my trip", "delegate", "flight_booking"))
    leaf = create_envelope(
        identity=rogue,
        # copies root's exact wording almost verbatim to game lexical similarity
        intent=make_intent("search for cheap flights to SF for my trip", "read", "flight_booking"),
        parent=root,
    )
    leaf.action_receipt = ActionReceipt(tool_name="charge_card", arguments={"amount": 750}, result_summary="charged $750")

    pairwise_score, pairwise_flags = engine.pairwise(root, leaf)
    action_score, action_flags = engine.action_grounding(leaf)

    # the text-gaming attack does inflate pairwise similarity...
    assert pairwise_flags == [], "expected the stuffed text to score well on pairwise, that's the attack"
    # ...but the rule-based action_type -> tool check doesn't care about wording at all
    assert any("action_mismatch" in f for f in action_flags), action_flags
    assert action_score < engine.thresholds.action_grounding_default


def test_vague_root_intent_still_flags_sensitive_resource_leaf():
    """Attack: make the root intent as vague as possible ("help me with
    my account") so that almost any leaf action can claim to be
    "close enough" under a pure text-similarity measure. The
    resource-escalation check doesn't grade on text similarity at
    all when a sensitive resource is newly touched, it's a hard
    categorical check, so vagueness in wording doesn't buy the
    attacker anything here.
    """
    engine = FidelityEngine()
    orch = AgentIdentity.generate("orch")
    rogue = AgentIdentity.generate("rogue")

    root = create_envelope(identity=orch, intent=make_intent("help me with my account", "delegate", "account_settings"))
    leaf = create_envelope(
        identity=rogue,
        intent=make_intent("help with account, adding a contact", "external_call", "account_settings"),
        parent=root,
    )
    leaf.action_receipt = ActionReceipt(
        tool_name="add_recovery_contact", arguments={"email": "attacker@example.com"}, result_summary="added attacker@example.com as recovery contact"
    )

    score, flags, threshold = engine.transitive(root, leaf)
    assert any("resource_escalation" in f for f in flags), flags


def test_boundary_hugging_multi_hop_drift_is_still_caught():
    """Attack: design each individual hop's wording to score just above
    the pairwise threshold, incrementally drifting toward a sensitive
    resource one small, plausible-looking step at a time (this is
    the threshold-probing pattern described in the design review:
    an attacker who can observe the verifier's tolerance crafts each
    step to stay just inside it). Every single pairwise check should
    pass by design. The transitive, resource-grounded check is what
    is supposed to catch it regardless of how carefully the pairwise
    hops were crafted.
    """
    engine = FidelityEngine()
    orch = AgentIdentity.generate("orch")
    a = AgentIdentity.generate("agent_a")
    b = AgentIdentity.generate("agent_b")
    c = AgentIdentity.generate("agent_c")

    root = create_envelope(identity=orch, intent=make_intent("help me manage my account", "delegate", "account_settings"))
    hop1 = create_envelope(identity=a, intent=make_intent("manage account details and settings", "read", "account_settings"), parent=root)
    hop2 = create_envelope(identity=b, intent=make_intent("manage account settings, review contact details", "write", "account_settings"), parent=hop1)
    leaf = create_envelope(identity=c, intent=make_intent("manage account contact details, add new contact", "external_call", "account_settings"), parent=hop2)
    leaf.action_receipt = ActionReceipt(
        tool_name="add_recovery_contact", arguments={"email": "attacker@example.com"}, result_summary="added attacker@example.com as recovery contact"
    )

    p1, p1_flags = engine.pairwise(root, hop1)
    p2, p2_flags = engine.pairwise(hop1, hop2)
    p3, p3_flags = engine.pairwise(hop2, leaf)
    assert p1_flags == [] and p2_flags == [] and p3_flags == [], "every crafted hop should pass pairwise individually"

    t_score, t_flags, t_threshold = engine.transitive(root, leaf)
    assert any("resource_escalation" in f for f in t_flags), t_flags


def test_fan_out_fan_in_not_modeled():
    """Honest documentation of a known architecture gap, not a passing
    security guarantee. get_chain() and the transitive check both
    assume a single linear parent pointer per envelope. A real
    multi-agent system can fan out (one root spawns three children)
    and fan in (results merged by a downstream agent that received
    input from more than one parent). This system has no
    representation for a merge point: an envelope has exactly one
    parent_envelope_id. If an attacker launders intent through one
    branch of a fan-out and the merge agent only cites the OTHER,
    clean branch as its parent when creating its own envelope, the
    laundering branch is invisible to forensic reconstruction even
    though it causally contributed to the final action. This is a
    real limitation, not fixed in this MVP, tracked in
    docs/ROADMAP.md as a v2 item (graph DB / multi-parent envelopes).
    This test just documents the assumption so it isn't a silent gap.
    """
    from aui.envelope.schema import Envelope

    assert "parent_envelope_id" in Envelope.model_fields
    field = Envelope.model_fields["parent_envelope_id"]
    # single Optional[str], not a list - confirms the single-parent assumption
    assert "list" not in str(field.annotation).lower()
