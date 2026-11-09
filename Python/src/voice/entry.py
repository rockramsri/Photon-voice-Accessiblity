"""LiveKit worker entrypoint — hold the pharmacy conversation for one order.

  python -m voice.entry dev       real calls (dispatcher dials, this joins)
  python -m voice.entry console   local test, no phone: set CONSOLE_ORDER_ID
                                   (and DRY_RUN=1 to skip real changes)

Loads context/<orderId>.json (written by the dispatcher), builds the session
state + agent, and on shutdown writes transcripts/<orderId>.json with the full
history, tool calls, and per-turn latency.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from livekit.agents import AgentServer, JobContext, cli

from auth.patient_token import mint_patient_jwt
from shared import config
from shared.context_store import load_context
from voice.agents.order_agent import build_order_agent
from voice.inbound import inbound_entrypoint
from voice.session_factory import build_session
from voice.session_state import SessionState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-18s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("photon.entry")

server = AgentServer()


@server.rtc_session(agent_name=config.AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    console = ctx.is_fake_job()
    order_id = meta.get("orderId") or config.CONSOLE_ORDER_ID

    # Inbound SIP call: no order yet. Authenticate the caller and schedule a callback.
    if not order_id and not console:
        await inbound_entrypoint(ctx)
        return

    # Prefer context from the job metadata (set by the dispatcher). On Railway the
    # worker and web run on different machines, so the local file won't exist here;
    # the file is only a same-machine/console fallback.
    if meta.get("seed") and meta.get("promptBlock"):
        doc = {"seed": meta["seed"], "promptBlock": meta["promptBlock"]}
    else:
        doc = load_context(order_id) if order_id else None
    if not doc:
        log.error("no context for order=%s (metadata + file both empty) — ending", order_id or "?")
        if not console:
            await ctx.delete_room()
        return

    seed = doc["seed"]
    jwt = meta.get("patientJwt") or await mint_patient_jwt(order_id, seed["patientId"])
    if not jwt:
        log.error("no patient JWT for order=%s — tools won't work", order_id)
    state = SessionState.from_seed(seed, jwt or "", config.ORG_NAME)
    agent = build_order_agent(state, doc["promptBlock"])
    session = build_session(state)
    log.info("order=%s can_change=%s tools=%d console=%s", order_id, state.can_offer_change,
             len(agent.tools), console)

    async def _finish() -> None:
        # Stateless: keep only a one-line outcome, then drop the PHI context file.
        line = json.dumps({
            "orderId": order_id,
            "endedAt": datetime.now(timezone.utc).isoformat(),
            "endReason": state.end_reason,
            "identityVerified": state.identity_verified,
            "finalPharmacyId": state.current_pharmacy_id,
        }) + "\n"
        with config.CALLS_LOG.open("a") as f:
            f.write(line)
        (config.CONTEXT_DIR / f"{order_id}.json").unlink(missing_ok=True)
        log.info("call finished order=%s reason=%s", order_id, state.end_reason)

    async def _hang_up() -> None:
        await ctx.delete_room()  # disconnects the SIP leg; no-op in console

    ctx.add_shutdown_callback(_finish)
    ctx.add_shutdown_callback(_hang_up)
    session.on("close", lambda _ev: ctx.shutdown(reason="session closed"))

    if not console:
        participant = await ctx.wait_for_participant()
        log.info("participant joined: %s", participant.identity)
    # record=False keeps call audio + transcripts (PHI) out of LiveKit Cloud.
    await session.start(agent=agent, room=ctx.room, record=False)


if __name__ == "__main__":
    cli.run_app(server)
