"""
Toy tools + the interceptor pattern.

Design rule that matters more than the tools themselves: in this
system, an agent is never allowed to call a tool directly. It must
go through `call_tool()`, which is the only thing with a real
reference to the underlying tool functions. That's what makes
action-grounding possible at all - if agents could reach tools
directly, they could act first and only report a receipt if they
felt like it, which defeats the entire premise. In a production
system this would be enforced by a sidecar/proxy that's the only
thing with network egress; here it's enforced by "the demo agents
literally have no other way to reach these functions."
"""
from __future__ import annotations

from typing import Callable


def search_flights(destination: str, **_) -> str:
    return f"found 3 flights to {destination}, cheapest $320"


def check_calendar(date: str, **_) -> str:
    return f"calendar checked for {date}: free"


def read_email(folder: str = "inbox", **_) -> str:
    return f"read 5 messages from {folder}"


def book_flight(destination: str, amount: float, **_) -> str:
    return f"booked flight to {destination} for ${amount}"


def charge_card(amount: float, purpose: str = "", **_) -> str:
    return f"charged ${amount} for {purpose}"


def send_email(to: str, subject: str = "", **_) -> str:
    return f"sent email to {to} ({subject})"


def update_calendar(date: str, event: str = "", **_) -> str:
    return f"updated calendar on {date}: {event}"


def add_recovery_contact(email: str, **_) -> str:
    return f"added {email} as account recovery contact"


TOOL_REGISTRY: dict[str, Callable[..., str]] = {
    "search_flights": search_flights,
    "check_calendar": check_calendar,
    "read_email": read_email,
    "book_flight": book_flight,
    "charge_card": charge_card,
    "send_email": send_email,
    "update_calendar": update_calendar,
    "add_recovery_contact": add_recovery_contact,
}


def call_tool(tool_name: str, arguments: dict) -> str:
    """The single choke point every simulated tool call passes through.

    Returns a plain-text result summary. Whoever calls this is
    responsible for attaching the resulting ActionReceipt to the
    envelope that authorized it - see aui.agents.harness.Agent.act().
    """
    if tool_name not in TOOL_REGISTRY:
        raise ValueError(f"unknown tool: {tool_name!r}")
    return TOOL_REGISTRY[tool_name](**arguments)
