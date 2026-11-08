"""The order agent: fixed persona + this order's prompt block + tools.

Write tools are only attached when the order can still change pharmacy, so a
locked order physically can't be changed from the call.
"""

from __future__ import annotations

from livekit.agents import Agent

from shared import config
from voice.session_state import SessionState
from voice.tools import READ_TOOLS, WRITE_TOOLS

PERSONA_FILE = config.ROOT / "prompts" / "voice_persona.txt"


class OrderAgent(Agent):
    """The order details stay OUT of the prompt until identity is verified, so the
    model physically can't disclose the medication, pharmacy, or price early."""

    def __init__(self, *, instructions: str, tools: list, order_block: str) -> None:
        super().__init__(instructions=instructions, tools=tools)
        self._order_block = order_block
        self._revealed = False

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions="Introduce yourself and who you're calling for, then ask if you're speaking with the patient by first name."
        )

    async def reveal_context(self) -> None:
        """Called by verify_identity on success — add the order block to the prompt."""
        if self._revealed or not self._order_block:
            return
        self._revealed = True
        await self.update_instructions(self.instructions + self._order_block)


def build_order_agent(state: SessionState, prompt_block: str) -> OrderAgent:
    persona = (PERSONA_FILE.read_text()
               .replace("{agent_name}", config.AGENT_DISPLAY_NAME)
               .replace("{org_name}", state.org_name)
               .replace("{patient_first_name}", state.first_name or "the patient"))
    tools = READ_TOOLS + (WRITE_TOOLS if state.can_offer_change else [])
    return OrderAgent(instructions=persona, tools=tools, order_block=prompt_block)
