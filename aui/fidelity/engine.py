"""
The Fidelity Engine: three verification stages.

  1. pairwise         - stated child intent vs stated parent intent
  2. action_grounding - actual tool call vs the intent that was declared
                         for THIS hop (this is what pure "compare two
                         paraphrases" designs miss entirely)
  3. transitive       - actual leaf action vs the ROOT intent, checked
                         directly rather than by chaining pairwise
                         scores together. This is what catches gradual,
                         each-hop-looks-fine drift.

Thresholds are per action_type/resource sensitivity, not global,
because a fixed global threshold either false-positives on benign
multi-hop task decomposition (e.g. "book a flight" -> "search prices"
-> "hold a seat" -> "charge the card" legitimately drifts in wording
at each hop) or false-negatives on high-stakes actions if it's set
loose enough to tolerate that decomposition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from aui.envelope.schema import ActionReceipt, Envelope
from aui.fidelity.backend import SimilarityBackend, LexicalSimilarityBackend, TfidfSimilarityBackend

_IDENT_RE = re.compile(r"[a-zA-Z0-9]+")


def describe_action(receipt: ActionReceipt) -> str:
    """Turn an action receipt into a plain, sentence-like string instead
    of Python-dict-shaped text.

    Found by benchmarking, not by inspection: the previous version of
    this ("tool_name {dict} result_summary") worked fine against the
    lexical backend, but scored badly under TF-IDF cosine similarity
    against natural-language root/parent intents, because a fixed
    reference corpus built from sentences doesn't represent curly
    braces and quote marks well, and 3 benign benchmark cases got
    false-flagged as a direct result. Splitting the tool name on
    underscores and pulling out just the alphanumeric tokens from the
    arguments gives the similarity backend something that actually
    reads like a sentence.
    """
    tool_words = receipt.tool_name.replace("_", " ")
    arg_words = " ".join(_IDENT_RE.findall(str(receipt.arguments)))
    return f"{tool_words} {arg_words} {receipt.result_summary}"

# Tool -> allowed action_type/resource pairs. This is the deterministic
# half of action-grounding: before ever asking "does this text sound
# like it matches," we check whether the tool that was actually called
# is even in the category the agent claimed it was operating in.
ALLOWED_ACTION_TOOL_MAP: dict[str, set[str]] = {
    "read": {"search_flights", "check_calendar", "read_email"},
    "write": {"send_email", "update_calendar"},
    "purchase": {"book_flight", "charge_card"},
    "external_call": {"send_email", "charge_card", "add_recovery_contact"},
    "delegate": set(),  # delegation doesn't call a tool directly
}

# Ground truth: which resource a tool ACTUALLY touches, independent of
# whatever the agent declared in its own structured.resource field.
#
# This exists because of a real adversarial finding (see
# aui/tests/test_adversarial.py::test_resource_misdeclaration_is_caught):
# the original design used the agent's SELF-DECLARED resource field for
# the transitive resource-escalation check. An attacker can just declare
# a benign resource ("account_settings") while its actual tool call
# targets a sensitive one (recovery_email), and the escalation check,
# trusting the declared field, never fires. Declared metadata about your
# own action is not evidence, it's a claim, same as the intent text
# itself. Anything used as a security check has to be grounded in what
# was independently observed (the tool that was actually called), not
# what the agent says about itself.
TOOL_RESOURCE_MAP: dict[str, str] = {
    "search_flights": "flight_booking",
    "book_flight": "flight_booking",
    "check_calendar": "calendar",
    "update_calendar": "calendar",
    "read_email": "email",
    "send_email": "email",
    "charge_card": "payment_api",
    "add_recovery_contact": "recovery_email",
}

# Resources considered high-sensitivity get stricter thresholds.
SENSITIVE_RESOURCES = {"payment_api", "recovery_email", "credentials", "bank_account"}

# Which sensitive sub-resources are a NORMAL, expected consequence of a
# given root task, vs. an escalation.
#
# Found by running the demo, not by theorizing: fixing the resource
# self-declaration bypass (see actual_resource_touched) made the
# escalation check too blunt and it started flagging a completely
# benign flight purchase, because "book me a flight" legitimately ends
# in a payment_api call. A flat rule ("any new sensitive resource is
# escalation") can't tell "buying the flight I asked for" apart from
# "quietly adding a new recovery contact while I asked you to review
# account settings." This allowlist is a blunt, hand-authored stand-in
# for what should really be a learned or policy-defined model of which
# sub-resources a task class can legitimately touch. It is intentionally
# an allowlist, not a denylist: unlisted combinations default to
# "escalation," which is the safer failure mode for a security control.
EXPECTED_SENSITIVE_SUBRESOURCES: dict[str, set[str]] = {
    "flight_booking": {"payment_api"},
    "hotel_booking": {"payment_api"},
    # account_settings deliberately does NOT list recovery_email here:
    # silently adding/changing a recovery contact while "reviewing
    # account settings" is exactly the escalation pattern this project
    # exists to catch, not a normal consequence of that task.
}


def actual_resource_touched(envelope: Envelope) -> str:
    """The ground-truth resource for this envelope: derived from the
    real tool call if one happened, falling back to the agent's
    declared resource only when nothing has actually executed yet
    (i.e. there's no independent evidence to check against)."""
    if envelope.action_receipt is not None:
        return TOOL_RESOURCE_MAP.get(envelope.action_receipt.tool_name, envelope.intent.structured.resource)
    return envelope.intent.structured.resource


@dataclass
class FidelityThresholds:
    # Note: re-tuned for the TfidfSimilarityBackend (see
    # fidelity/backend.py). Retuning was necessary, not optional: the
    # backend swap shifted score distributions, and one adversarial
    # test (cumulative-drift, hop1->hop2) that passed cleanly under the
    # lexical backend came in at 0.537 against the old 0.55 sensitive
    # threshold under TF-IDF, confirmed by actually running the suite
    # after the swap, not assumed. Still loose enough not to
    # false-positive on the benign scenario's payment step. When
    # EmbeddingSimilarityBackend eventually replaces this (blocked on
    # torch installing, see backend.py), expect another retuning pass,
    # a real semantic model can likely support stricter thresholds
    # without the same false-positive risk.
    pairwise_default: float = 0.15
    pairwise_sensitive: float = 0.45
    action_grounding_default: float = 0.5
    transitive_default: float = 0.15
    transitive_sensitive: float = 0.5

    def pairwise(self, resource: str) -> float:
        return self.pairwise_sensitive if resource in SENSITIVE_RESOURCES else self.pairwise_default

    def transitive(self, resource: str) -> float:
        return self.transitive_sensitive if resource in SENSITIVE_RESOURCES else self.transitive_default


@dataclass
class FidelityEngine:
    # Default is LexicalSimilarityBackend, not TfidfSimilarityBackend,
    # on purpose and by measurement: see docs/EVALUATION.md. The TF-IDF
    # backend was built as an upgrade attempt, iteratively debugged
    # (found and fixed a missing-stemming bug and a stopword/stemmer
    # mismatch), and still scored worse on an 18-case labeled benchmark
    # than this simpler baseline (0.8 vs 1.0 precision). Shipping the
    # backend that's actually better, not the one that sounds fancier.
    backend: SimilarityBackend = field(default_factory=LexicalSimilarityBackend)
    thresholds: FidelityThresholds = field(default_factory=FidelityThresholds)

    # ---- stage 1: pairwise -------------------------------------------------
    def pairwise(self, parent: Envelope, child: Envelope) -> tuple[float, list[str]]:
        score = self.backend.score(parent.intent.raw_text, child.intent.raw_text)
        threshold = self.thresholds.pairwise(child.intent.structured.resource)
        flags = []
        if score < threshold:
            flags.append(f"low_pairwise_fidelity(score={score:.2f},threshold={threshold:.2f})")
        return score, flags

    # ---- stage 2: action grounding -----------------------------------------
    def action_grounding(self, envelope: Envelope) -> tuple[float, list[str]]:
        """Does the actual tool call match what this hop *said* it would do."""
        if envelope.action_receipt is None:
            return 1.0, []  # nothing executed yet, nothing to check

        flags: list[str] = []
        structured = envelope.intent.structured
        tool = envelope.action_receipt.tool_name

        allowed_tools = ALLOWED_ACTION_TOOL_MAP.get(structured.action_type, set())
        rule_pass = tool in allowed_tools
        if not rule_pass:
            flags.append(
                f"action_mismatch(declared_action_type={structured.action_type!r},"
                f"actual_tool={tool!r},not_in_allowed_set)"
            )

        # ground-truth resource check: catches an agent that declared
        # one (benign) resource but whose real tool call touches a
        # DIFFERENT SENSITIVE one, regardless of whether action_type
        # happens to cover both. This is independent of the rule_pass
        # check above: a tool can be in the right action_type category
        # and still be touching a resource the agent never disclosed.
        #
        # Only flags when the true resource is sensitive AND not a
        # normal downstream consequence of the declared resource (see
        # EXPECTED_SENSITIVE_SUBRESOURCES). Without that allowance this
        # false-positived on a completely benign flight purchase: the
        # agent declares resource="flight_booking" and its tool call
        # legitimately touches payment_api to pay for the flight,
        # which is not a lie, it's just the multi-resource nature of a
        # real task. Found by running the demo after adding this
        # check, not by reasoning about it in advance.
        true_resource = TOOL_RESOURCE_MAP.get(tool)
        expected_here = EXPECTED_SENSITIVE_SUBRESOURCES.get(structured.resource, set())
        resource_lie = (
            true_resource is not None
            and true_resource != structured.resource
            and true_resource in SENSITIVE_RESOURCES
            and true_resource not in expected_here
        )
        if resource_lie:
            flags.append(
                f"resource_misdeclaration(declared_resource={structured.resource!r},"
                f"actual_resource={true_resource!r})"
            )

        # constraint checks, e.g. "max_amount:50usd" vs actual charge amount
        constraint_score = 1.0
        for constraint in structured.constraints:
            if constraint.startswith("max_amount:"):
                try:
                    limit = float(constraint.split(":", 1)[1].replace("usd", "").strip())
                    actual = float(envelope.action_receipt.arguments.get("amount", 0))
                    if actual > limit:
                        constraint_score = 0.0
                        flags.append(f"constraint_violation(max_amount={limit},actual={actual})")
                except (ValueError, IndexError):
                    pass
            if constraint.startswith("scope:read_only"):
                if structured.action_type not in ("read",):
                    constraint_score = min(constraint_score, 0.0)
                    flags.append("constraint_violation(scope:read_only but action_type is not read)")

        # semantic sanity check between declared intent text and a plain
        # description of what the tool actually did
        action_desc = describe_action(envelope.action_receipt)
        text_score = self.backend.score(envelope.intent.raw_text, action_desc)

        score = (1.0 if rule_pass else 0.0) * 0.5 + constraint_score * 0.3 + text_score * 0.2
        if resource_lie:
            score = min(score, 0.2)
        threshold = self.thresholds.action_grounding_default
        if score < threshold and not flags:
            flags.append(f"low_action_grounding(score={score:.2f},threshold={threshold:.2f})")
        return score, flags

    # ---- stage 3: transitive ------------------------------------------------
    def transitive(self, root: Envelope, leaf: Envelope) -> tuple[float, list[str]]:
        """Compare the leaf's actual action directly against the ROOT
        intent, not against its immediate parent. This is the check
        that catches laundering that survives every individual
        pairwise hop because each step only drifted a little.
        """
        flags: list[str] = []

        if leaf.action_receipt is not None:
            leaf_desc = describe_action(leaf.action_receipt)
        else:
            leaf_desc = leaf.intent.raw_text

        score = self.backend.score(root.intent.raw_text, leaf_desc)

        # resource-sensitivity check: root never mentioned touching a
        # sensitive resource, but the leaf action does. This catches
        # cases where wording drift alone wouldn't trip a text-similarity
        # threshold (e.g. innocuous-sounding action, sensitive target).
        #
        # Uses actual_resource_touched(), NOT leaf.intent.structured.resource
        # directly. Using the self-declared field here was a real bug
        # found during red-teaming: an agent can just declare a benign
        # resource while its real tool call hits a sensitive one, and a
        # check that trusts the declared field never fires. Grounding
        # this in the real tool call closes that bypass.
        root_resource = root.intent.structured.resource
        leaf_resource = actual_resource_touched(leaf)
        expected = EXPECTED_SENSITIVE_SUBRESOURCES.get(root_resource, set())
        is_escalation = leaf_resource in SENSITIVE_RESOURCES and leaf_resource != root_resource and leaf_resource not in expected
        if is_escalation:
            score = min(score, 0.2)
            flags.append(
                f"resource_escalation(root_resource={root_resource!r},leaf_resource={leaf_resource!r})"
            )

        # Use the strict sensitive threshold only when this is actually
        # an escalation. A sensitive resource that's an EXPECTED
        # consequence of the root task (flight_booking -> payment_api)
        # shouldn't be held to the stricter bar meant for unexpected
        # sensitive touches, that was a second false positive found by
        # running the demo: the escalation flag correctly didn't fire
        # for a normal purchase, but the threshold lookup still used
        # leaf_resource's raw sensitivity and failed the score anyway.
        threshold = self.thresholds.transitive(leaf_resource) if is_escalation else self.thresholds.transitive(root_resource)
        if score < threshold and not is_escalation:
            flags.append(f"cumulative_drift(score={score:.2f},threshold={threshold:.2f})")

        return score, flags
