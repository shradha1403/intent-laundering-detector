"""
Guards against a real maintainability risk flagged in a security audit:
the detection policy is spread across three hand-authored maps in two
different modules (aui/interceptor/tools.py's TOOL_REGISTRY, and
aui/fidelity/engine.py's TOOL_RESOURCE_MAP + ALLOWED_ACTION_TOOL_MAP),
and nothing enforced that they stay in sync. Add a tool to the
interceptor without also adding it to TOOL_RESOURCE_MAP, and
action-grounding/transitive silently stop being able to ground that
tool's resource at all - `actual_resource_touched()` just falls back
to trusting the agent's own declared resource for it, exactly the
self-declaration bypass the rest of this project exists to close.

These tests don't fix that coupling (that would mean a real policy
loader, tracked as a v2 item, see docs/ROADMAP.md), but they make the
drift impossible to introduce silently: any future edit that breaks
the sync fails a test instead of quietly degrading detection for
whatever tool got left out.
"""
from __future__ import annotations

from aui.interceptor.tools import TOOL_REGISTRY
from aui.fidelity.engine import ALLOWED_ACTION_TOOL_MAP, TOOL_RESOURCE_MAP


def test_every_registered_tool_has_a_ground_truth_resource():
    """Every tool an agent can actually call must have an entry in
    TOOL_RESOURCE_MAP, or action-grounding/transitive can't ground its
    resource and silently fall back to trusting the agent's own claim."""
    registered = set(TOOL_REGISTRY)
    mapped = set(TOOL_RESOURCE_MAP)
    missing = registered - mapped
    assert not missing, (
        f"tools registered in interceptor but missing from TOOL_RESOURCE_MAP: {missing} "
        "- action-grounding/transitive cannot ground these tools' resources"
    )


def test_tool_resource_map_has_no_stale_entries():
    """The reverse gap: an entry in TOOL_RESOURCE_MAP for a tool that no
    longer exists in the interceptor is dead policy, not a security gap,
    but it's exactly the kind of thing that quietly rots and misleads
    the next person reading the maps."""
    registered = set(TOOL_REGISTRY)
    mapped = set(TOOL_RESOURCE_MAP)
    stale = mapped - registered
    assert not stale, f"TOOL_RESOURCE_MAP references tools that no longer exist: {stale}"


def test_every_registered_tool_is_reachable_via_some_action_type():
    """Every real tool should be listed under at least one action_type in
    ALLOWED_ACTION_TOOL_MAP. A tool missing from every category means no
    declared action_type can ever legitimately call it, so action
    grounding would flag every real use of that tool as a mismatch."""
    registered = set(TOOL_REGISTRY)
    reachable = set()
    for tools in ALLOWED_ACTION_TOOL_MAP.values():
        reachable |= tools
    unreachable = registered - reachable
    assert not unreachable, (
        f"tools registered in interceptor but not allowed under any action_type: {unreachable} "
        "- every real use of these tools would be flagged as action_mismatch"
    )


def test_allowed_action_tool_map_has_no_stale_entries():
    """The reverse gap for ALLOWED_ACTION_TOOL_MAP: an allowed tool name
    that isn't actually registered anywhere is dead/misleading policy."""
    registered = set(TOOL_REGISTRY)
    referenced = set()
    for tools in ALLOWED_ACTION_TOOL_MAP.values():
        referenced |= tools
    stale = referenced - registered
    assert not stale, f"ALLOWED_ACTION_TOOL_MAP references tools that no longer exist: {stale}"
