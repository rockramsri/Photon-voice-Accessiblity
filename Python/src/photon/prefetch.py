"""Prefetch — gather all patient-facing order context before we dial.

Fires the 5 patient-API queries in PARALLEL. We dial only after this returns,
so the budget is generous — a slow API before the call costs the patient
nothing; only silence DURING the call matters. The cap (5 min) is just a safety
net against a hung connection. Any call still over the cap is dropped and logged;
we keep the rest and report a `completeness` map. If the JWT is missing we fall
back to a minimal provider-API read and mark the context `partial`.

Returns a raw dict (no voice shaping here — that's voice/shape_context.py):
  {order, offers, pharmacies, faqs, nearby, completeness, partial}
"""

from __future__ import annotations

import asyncio
import logging

from photon import client, queries
from shared import config

log = logging.getLogger("photon.prefetch")

# Generous — prefetch runs BEFORE we dial, so a slow API costs the patient
# nothing. These are just safety caps against a hung connection.
TOTAL_BUDGET = 300.0
PER_CALL = 60.0


async def _safe(coro, timeout: float = PER_CALL):
    """Await `coro` with a timeout; return its data dict or None on any failure."""
    try:
        data = await asyncio.wait_for(coro, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — a slow/failed call just drops out
        log.warning("prefetch call failed/timed out: %s", exc)
        return None
    if data.get("errors"):
        log.warning("prefetch call returned GraphQL errors: %s", data["errors"])
    return (data.get("data") or {})


async def prefetch(jwt: str, order_id: str, point: tuple[float, float] | None) -> dict:
    """Run the 5 patient-API queries in parallel and collect what returns."""
    names = ["order", "offers", "pharmacies", "faqs", "nearby"]
    coros = [
        _safe(client.patient_api(queries.ORDER, {"id": order_id}, jwt=jwt)),
        _safe(client.patient_api(queries.OFFER_BUNDLES, {"orderId": order_id}, jwt=jwt)),
        _safe(client.patient_api(queries.PHARMACIES, {}, jwt=jwt)),
        _safe(client.patient_api(queries.FAQS, {"orderId": order_id}, jwt=jwt)),
        _nearby(jwt, point),
    ]
    try:
        results = await asyncio.wait_for(asyncio.gather(*coros), timeout=TOTAL_BUDGET)
    except asyncio.TimeoutError:
        log.warning("prefetch exceeded %.0fs total budget", TOTAL_BUDGET)
        results = [None] * len(names)

    ctx: dict = {"partial": False}
    completeness: dict[str, bool] = {}
    for name, res in zip(names, results):
        value = _extract(name, res)
        ctx[name] = value
        completeness[name] = value is not None
    ctx["completeness"] = completeness
    log.info("prefetch order=%s completeness=%s", order_id, completeness)
    missing = [name for name, ok in completeness.items() if not ok]
    if missing:
        log.warning("prefetch order=%s INCOMPLETE — missing: %s", order_id, ", ".join(missing))
    return ctx


async def _nearby(jwt: str, point: tuple[float, float] | None):
    """20 nearest + 3 nearest 24-hour stores (fallback when nothing close is open).

    Photon has no open-now filter or price sort, so we fetch wide and let
    shape_context.rank_nearby pick a balanced three.
    """
    if not point:
        log.info("no geocoded point — skipping nearby query")
        return None
    lat, lng = point
    base = {"location": {"latitude": lat, "longitude": lng, "radius": 100}, "includePrice": True}
    near, all_day = await asyncio.gather(
        _safe(client.patient_api(queries.PHARMACIES_BY_LOCATION, {**base, "limit": 20}, jwt=jwt)),
        _safe(client.patient_api(queries.PHARMACIES_BY_LOCATION, {**base, "limit": 3, "is24hr": True}, jwt=jwt)),
    )
    if near is None and all_day is None:
        return None
    rows = {p["id"]: p for data in (near, all_day)
            for p in (data or {}).get("pharmaciesByLocation") or [] if p.get("id")}
    return {"pharmaciesByLocation": list(rows.values())}


def _extract(name: str, data: dict | None):
    """Pull the field out of a GraphQL `data` dict; None if the call dropped."""
    if data is None:
        return None
    field = {
        "order": "order",
        "offers": "offerBundles",
        "pharmacies": "pharmacies",
        "faqs": "faqs",
        "nearby": "pharmaciesByLocation",
    }[name]
    return data.get(field)


async def prefetch_fallback(order_id: str) -> dict:
    """Minimal context via the provider API (Bearer) when there's no JWT."""
    data = await _safe(client.provider_api(queries.PROVIDER_FALLBACK, {"id": order_id},
                                           token=config.PHOTON_TOKEN))
    order = (data or {}).get("order")
    log.info("prefetch FALLBACK order=%s order_present=%s", order_id, order is not None)
    return {
        "partial": True,
        "order": order,
        "offers": None,
        "pharmacies": None,
        "faqs": None,
        "nearby": None,
        "completeness": {"order": order is not None, "offers": False,
                         "pharmacies": False, "faqs": False, "nearby": False},
    }
