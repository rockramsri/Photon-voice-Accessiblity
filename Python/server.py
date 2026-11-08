"""Photon webhook server — receive order events, greet the patient by voice.

Extends the photon-webhook-test/server.js pattern in Python (aiohttp, like the
reference repo's channels/webhook.py):

  POST /photon/webhooks  raw-body capture -> 200 immediately -> process async
  GET  /health           liveness probe

`photon:order:created` triggers a call. Photon sends its own text when a change
lands, so we don't track it here. Signature verification is log-only (see
photon/events.verify_signature).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from aiohttp import web

import dispatcher
from photon import events
from shared import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-18s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("photon.server")

LOG_FILE = config.STATE_FILE.parent.parent / "webhooks.jsonl"

CREATED = "photon:order:created"


async def _process_event(ev: dict) -> None:
    """Route one CloudEvent: created -> call. Photon sends its own change confirmations."""
    ev_type = ev.get("type")
    try:
        if ev_type == CREATED:
            await dispatcher.handle_order_created(ev.get("data") or {})
        else:
            log.info("ignoring event type=%s (id=%s)", ev_type, ev.get("id"))
    except Exception as exc:  # noqa: BLE001 — one bad event must not kill the server
        log.exception("handler failed for %s: %s", ev_type, exc)


async def handle_webhook(request: web.Request) -> web.Response:
    raw = await request.read()
    received_at = datetime.now(timezone.utc).isoformat()

    # Signature check — LOG ONLY for now (never rejects).
    events.verify_signature(
        raw,
        request.headers.get(events.TIMESTAMP_HEADER, ""),
        request.headers.get(events.SIGNATURE_HEADER, ""),
        config.SHARED_SECRET,
    )

    parsed = events.parse_events(raw)
    log.info("webhook received: %d event(s) [%s]",
             len(parsed), ", ".join(e.get("type", "?") for e in parsed) or "none")

    # Append the raw delivery for later inspection.
    try:
        line = json.dumps({"receivedAt": received_at,
                           "headers": dict(request.headers),
                           "events": parsed}) + "\n"
        LOG_FILE.write_text((LOG_FILE.read_text() if LOG_FILE.exists() else "") + line)
    except Exception as exc:  # noqa: BLE001 — logging must not break the 200 response
        log.error("could not append to webhooks.jsonl: %s", exc)

    # Respond 200 immediately; process out of band so Photon never waits on us.
    for ev in parsed:
        asyncio.create_task(_process_event(ev))

    return web.json_response({"ok": True})


async def handle_health(_: web.Request) -> web.Response:
    return web.Response(text="up")


async def _delayed_callback(order_id: str, patient_id: str) -> None:
    """Wait the configured delay, then dial the patient about this order."""
    await asyncio.sleep(config.CALLBACK_DELAY_S)
    log.info("callback firing order=%s after %ds", order_id, config.CALLBACK_DELAY_S)
    await dispatcher.handle_order_created({"id": order_id, "patient": {"id": patient_id}}, force=True)


async def handle_callback(request: web.Request) -> web.Response:
    """Inbound agent enqueues an authenticated callback here (loopback only)."""
    body = await request.json()
    order_id, patient_id = body.get("orderId"), body.get("patientId")
    if not order_id or not patient_id:
        return web.json_response({"ok": False, "error": "orderId and patientId required"}, status=400)
    log.info("callback queued order=%s in %ds", order_id, config.CALLBACK_DELAY_S)
    asyncio.create_task(_delayed_callback(order_id, patient_id))
    return web.json_response({"ok": True, "delaySeconds": config.CALLBACK_DELAY_S})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/photon/webhooks", handle_webhook)
    app.router.add_post("/internal/callback", handle_callback)
    app.router.add_get("/health", handle_health)
    return app


if __name__ == "__main__":
    log.info("listening on http://localhost:%d  (POST /photon/webhooks, GET /health)",
             config.PORT)
    web.run_app(build_app(), port=config.PORT)
