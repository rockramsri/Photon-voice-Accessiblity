"""Outbound calls — dispatch the agent into a room, then dial out via SIP.

Mirrors Rock-voice-scheduler-engine/channels/outbound.py: agent-first ordering
so the callee never answers into an empty room. Never raises — any config or
SIP/dispatch error comes back as {"ok": False, "error": ...}.
"""

from __future__ import annotations

import logging

from livekit import api

from shared import config

log = logging.getLogger("channels.outbound")


async def place_call(to: str, room_name: str, metadata: str = "") -> dict:
    """Dispatch the greeting agent into `room_name`, then ring `to` (E.164)."""
    if not config.SIP_OUTBOUND_TRUNK_ID:
        log.error("SIP_OUTBOUND_TRUNK_ID is not set — cannot place outbound call")
        return {"ok": False, "to": to, "error": "outbound SIP trunk not configured"}

    lk = api.LiveKitAPI()
    try:
        # Named worker needs an explicit dispatch; the agent reads its context
        # from this metadata (orderId, patient first name, pharmacyId, ...).
        await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
            agent_name=config.AGENT_NAME, room=room_name, metadata=metadata,
        ))
        participant = await lk.sip.create_sip_participant(api.CreateSIPParticipantRequest(
            sip_trunk_id=config.SIP_OUTBOUND_TRUNK_ID,
            sip_call_to=to,
            sip_number=config.TWILIO_CALLER_NUMBER,
            room_name=room_name,
            participant_identity=f"phone-{to}",
            wait_until_answered=False,
        ))
        log.info("outbound call -> %s room=%s", to, room_name)
        return {"ok": True, "to": to, "room": room_name,
                "participant": participant.participant_identity}
    except Exception as exc:  # noqa: BLE001 — any SIP/dispatch failure is a result, not a crash
        log.error("outbound call -> %s FAILED: %s", to, exc)
        return {"ok": False, "to": to, "room": room_name, "error": str(exc)}
    finally:
        await lk.aclose()
