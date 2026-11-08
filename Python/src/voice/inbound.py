"""Inbound agent — authenticate a caller, then schedule an outbound callback.

No order details are ever discussed inbound. The agent collects the account
phone + date of birth, finds the caller's latest order (Clinical API, M2M
token), verifies the DOB, and POSTs the order to the server, which dials back
after CALLBACK_DELAY_S. The callback runs the normal outbound order flow.
"""

from __future__ import annotations

import logging
import re

import aiohttp
from livekit.agents import Agent, JobContext, RunContext, StopResponse, function_tool

from auth.photon_auth import get_m2m_token
from photon import client, queries
from shared import config
from voice.session_factory import build_session

log = logging.getLogger("photon.inbound")

INSTRUCTIONS = f"""You are {config.AGENT_DISPLAY_NAME}, a phone coordinator for {config.ORG_NAME}. You're on a PHONE CALL.

HOW TO SPEAK
- Plain spoken words only. No markdown, lists, symbols, or emojis.
- One or two short sentences per turn. One question per turn, never two.
- Numbers in words. Read nothing back that you weren't given.
- Lead each turn with one warm expression tag and vary it, e.g. <expr type="expression" label="happy"/> to greet, <expr type="expression" label="empathetic"/> if they sound worried, <expr type="expression" label="grateful"/> when they thank you. Never put a tag inside a phone number or a date.

WHAT YOU CAN DO
- You CANNOT look up, confirm, or discuss any order, medication, pharmacy, or price on this call. You can ONLY arrange a callback about their order.
- Greet them and ask how you can help. If it's anything about a prescription, order, pharmacy, or delivery, offer to have someone call them back about it.
- To arrange it you need two things, asked ONE at a time: first the phone number on their account, then their date of birth.
- Once you have both, call request_callback with the phone digits and the month, day, and year. Then say exactly what its result tells you.
- Never reveal or guess a date of birth. Never promise anything beyond a callback. If they're upset or ask for a person, give them the support number and end the call.
"""


class InboundAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS, tools=[request_callback])

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=f"Greet the caller warmly, say you're {config.AGENT_DISPLAY_NAME} from {config.ORG_NAME}, and ask how you can help."
        )


def _normalize_phone(raw: str) -> str | None:
    """Spoken digits -> E.164 US (+1XXXXXXXXXX). None if not 10/11 digits."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    return None


async def _end(context: RunContext, line: str) -> str:
    """Speak a final line, then hang up (mirrors the order agent's end_call)."""
    handle = context.session.say(line)
    handle.add_done_callback(lambda _: context.session.shutdown())
    raise StopResponse


@function_tool
async def request_callback(context: RunContext, phone_digits: str, month: int, day: int, year: int) -> str:
    """Find the caller's latest order by phone, verify their date of birth, and schedule a callback.

    Args:
        phone_digits: The account phone number the caller said, digits only (10 digits, or 11 with a leading 1).
        month: Date of birth month, 1 to 12.
        day: Date of birth day, 1 to 31.
        year: Date of birth four-digit year, e.g. 2002.
    """
    state = context.userdata
    phone = _normalize_phone(phone_digits)
    if not phone:
        return "That phone number wasn't ten digits. Ask them to say it again, digit by digit."

    try:
        token = await get_m2m_token()
        data = await client.clinical_api(queries.ORDERS_BY_PHONE, {"phone": phone}, token=token, timeout=8)
        orders = (data.get("data") or {}).get("orders") or []
    except Exception as exc:  # noqa: BLE001 — a lookup failure is not the caller's fault
        log.warning("inbound lookup failed: %s", exc)
        return "Our system is slow right now. Apologize and tell them to try again shortly."

    support = config.SUPPORT_SMS_NUMBER
    if not orders:
        return await _end(context, f'<expr type="expression" label="empathetic"/> I\'m sorry, I couldn\'t find an order under that number. Please call us at {support} and we\'ll help. Goodbye.')

    order = orders[0]
    dob = ((order.get("patient") or {}).get("dateOfBirth") or "")[:10]
    state["attempts"] = state.get("attempts", 0) + 1
    try:
        y, m, d = (int(x) for x in dob.split("-"))
    except ValueError:
        return await _end(context, f'<expr type="expression" label="empathetic"/> I\'m sorry, I can\'t verify that right now. Please call us at {support}. Goodbye.')

    if (month, day, year) != (m, d, y):
        if state["attempts"] < 3:
            return "That date of birth doesn't match our records. Ask them to say the month, day, and year again, slowly."
        return await _end(context, f'<expr type="expression" label="empathetic"/> I\'m sorry, that still doesn\'t match, so I can\'t set up a callback. Please call us at {support}. Goodbye.')

    order_id, patient_id = order["id"], (order.get("patient") or {}).get("id")
    url = config.DISPATCH_CALLBACK_URL or f"http://localhost:{config.PORT}/internal/callback"
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(url, json={"orderId": order_id, "patientId": patient_id}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                ok = resp.status == 200
    except Exception as exc:  # noqa: BLE001
        log.warning("inbound callback enqueue failed order=%s: %s", order_id, exc)
        ok = False
    if not ok:
        return await _end(context, f'<expr type="expression" label="empathetic"/> I\'m sorry, I couldn\'t set that up right now. Please call us at {support}. Goodbye.')

    log.info("inbound callback scheduled order=%s", order_id)
    return await _end(context, '<expr type="expression" label="grateful"/> Thank you, you\'re verified. If there\'s anything for your order, we\'ll call you right back about it. Take care, goodbye.')


async def inbound_entrypoint(ctx: JobContext) -> None:
    """Answer an inbound call; no order metadata needed."""
    session = build_session(userdata={})
    await ctx.wait_for_participant()
    log.info("inbound call connected room=%s", ctx.room.name)
    await session.start(agent=InboundAgent(), room=ctx.room, record=False)
