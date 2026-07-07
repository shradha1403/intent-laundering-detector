"""
A deliberately minimal agent harness.

We hand-roll this instead of wiring up LangGraph or CrewAI. Reasoning
(from the design review): pulling in a full orchestration framework
costs a day of learning its abstractions, and the point of this
project is the verification layer, not the orchestration layer.
A hand-rolled harness is also more controllable for scripting a
specific, repeatable attack scenario, which matters more for a demo
than framework realism does. Plugging this into a real framework is
a documented v2 stretch goal, not a hidden gap (see docs/ROADMAP.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aui.broker.service import BrokerService
from aui.envelope.schema import Envelope, Intent, StructuredIntent


@dataclass
class Agent:
    agent_id: str
    broker: BrokerService

    def __post_init__(self):
        self.broker.register_agent(self.agent_id)

    def delegate(
        self,
        raw_text: str,
        action_type: str,
        resource: str,
        constraints: Optional[list[str]] = None,
        parent: Optional[Envelope] = None,
    ) -> Envelope:
        """Create a new envelope stating this agent's sub-intent."""
        intent = Intent(
            raw_text=raw_text,
            structured=StructuredIntent(action_type=action_type, resource=resource, constraints=constraints or []),
        )
        return self.broker.create_envelope(
            self.agent_id, intent, parent.envelope_id if parent else None
        )

    def act(self, envelope: Envelope, tool_name: str, arguments: dict) -> Envelope:
        """Actually call a tool through the interceptor and attach the
        real receipt to `envelope`. This is the only way any tool
        call happens in this system - see aui/interceptor/tools.py."""
        return self.broker.execute_action(envelope.envelope_id, tool_name, arguments)
