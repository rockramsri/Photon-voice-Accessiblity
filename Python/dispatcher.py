"""Dispatcher — turn a fresh order:created event into one outbound call.

Flow: dedupe -> mint order JWT -> fetch patient -> geocode address ->
prefetch (5 calls) -> shape prompt block + seed -> write context file -> dial.
Only `photon:order:created` reaches here.
"""

from __future__ import annotations

import json
import logging

from auth.patient_token import mint_patient_jwt
from channels import outbound
from photon import dedupe
from photon.geocode import geocode_address
from photon.patient import fetch_patient
from photon.prefetch import prefetch
from shared import config
from shared.context_store import save_context
from voice.shape_context import build_prompt_block, build_seed

log = logging.getLogger("photon.dispatcher")


async def handle_order_created(data: dict, *, force: bool = False) -> None:
    """Process one order:created `data` block. Never raises.

    `force=True` (inbound callback) bypasses dedupe: the patient explicitly asked
    us to call back about this order, even if a webhook already rang it earlier.
    """
    order_id = data.get("id")
    if not order_id:
        log.warning("order:created with no data.id — ignoring")
        return

    # DEDUPE: webhooks redeliver; never start two calls for one order.
    if not force and dedupe.seen(order_id):
        log.info("order %s already processed — skipping", order_id)
        return

    # No JWT -> no patient phone -> no dial. mint_patient_jwt already retries
    # once on an auth error, so a failure here is real.
    patient_id = (data.get("patient") or {}).get("id")
    if not patient_id:
        log.error("order %s has no patient id — cannot mint JWT, not dialing", order_id)
        return
    patient_jwt = await mint_patient_jwt(order_id, patient_id)
    if not patient_jwt:
        log.error("patient JWT mint FAILED for order %s — not dialing", order_id)
        return

    patient = await fetch_patient(patient_jwt)
    to = config.TEST_PHONE_OVERRIDE or patient["phone"]
    if not to:
        log.error("order %s has no patient phone and no TEST_PHONE_OVERRIDE — not dialing", order_id)
        return

    # Context for the agent. A partial prefetch still proceeds with what returned.
    point = await geocode_address(patient["address"])
    ctx = await prefetch(patient_jwt, order_id, point)
    save_context(order_id, build_prompt_block(ctx), build_seed(ctx, order_id, point, patient, patient_id))

    room_name = f"photon-order-{order_id}"
    metadata = json.dumps({"orderId": order_id, "patientJwt": patient_jwt})
    log.info("dialing order=%s -> %s%s", order_id, to,
             " (override)" if config.TEST_PHONE_OVERRIDE else "")
    result = await outbound.place_call(to, room_name=room_name, metadata=metadata)
    if result.get("ok"):
        dedupe.mark(order_id)
        log.info("order %s dialed, room=%s", order_id, result.get("room"))
    else:
        log.error("order %s dial failed: %s", order_id, result.get("error"))
