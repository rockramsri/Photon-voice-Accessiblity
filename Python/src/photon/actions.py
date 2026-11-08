"""In-call Photon calls used by the agent's tools (patient-api, order JWT).

Responses are validated with small pydantic models; anything unexpected
raises PhotonError so the tool can fall back to "you'll get a text link".
Logs carry operation + orderId + pharmacyId only (no PHI, no token).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from pydantic import BaseModel

from photon import client, queries

log = logging.getLogger("photon.actions")


class PhotonError(Exception):
    pass


class PharmacyRef(BaseModel):
    id: str
    name: str | None = None


class Fulfillment(BaseModel):
    type: str | None = None
    state: str | None = None


class OrderStatus(BaseModel):
    id: str
    state: str | None = None
    isReroutable: bool = False
    readyByDay: str | None = None
    readyByTime: str | None = None
    pharmacy: PharmacyRef | None = None
    fulfillment: Fulfillment | None = None


async def _call(op: str, query: str, variables: dict, jwt: str, *, order_id: str = "",
                pharmacy_id: str = "") -> dict:
    log.info("photon %s order=%s pharmacy=%s", op, order_id or "-", pharmacy_id or "-")
    resp = await client.patient_api(query, variables, jwt=jwt)
    if resp.get("errors"):
        log.warning("photon %s failed: %s", op, [e.get("message") for e in resp["errors"]])
        raise PhotonError(op)
    return resp.get("data") or {}


async def search_pharmacies(jwt: str, lat: float, lng: float, *, name: str | None = None,
                            is24hr: bool = False, limit: int = 50) -> list[dict]:
    """One wide page sorted by distance; the tool ranks/filters it locally."""
    variables = {
        "location": {"latitude": lat, "longitude": lng, "radius": 100},
        "limit": limit, "includePrice": True,
        "name": name or None, "is24hr": is24hr or None,
    }
    data = await _call("pharmaciesByLocation", queries.PHARMACIES_BY_LOCATION, variables, jwt)
    return data.get("pharmaciesByLocation") or []


async def order_status(jwt: str, order_id: str) -> OrderStatus:
    data = await _call("getOrderStatus", queries.ORDER_STATUS, {"id": order_id}, jwt, order_id=order_id)
    if not data.get("order"):
        raise PhotonError("order missing")
    return OrderStatus.model_validate(data["order"])


async def _mutate(op: str, query: str, field: str, variables: dict, jwt: str,
                  order_id: str, pharmacy_id: str) -> None:
    data = await _call(op, query, variables, jwt, order_id=order_id, pharmacy_id=pharmacy_id)
    if data.get(field) is not True:
        raise PhotonError(f"{op} returned {data.get(field)!r}")


async def reroute(jwt: str, order_id: str, pharmacy_id: str, price_quoted: bool) -> None:
    """Move an order that ALREADY has a pharmacy. On a pharmacy-less order Photon
    returns true but changes nothing — use set_order_pharmacy for those."""
    await _mutate("rerouteOrder", queries.REROUTE_ORDER, "rerouteOrder", {
        "orderId": order_id, "pharmacyId": pharmacy_id,
        "patientSelectedPrice": price_quoted, "reason": "patient phone selection",
    }, jwt, order_id, pharmacy_id)


async def set_order_pharmacy(jwt: str, order_id: str, pharmacy_id: str, price_quoted: bool,
                             when: datetime | None = None) -> None:
    """First pharmacy choice for an order (optionally with a pickup time)."""
    variables = {"orderId": order_id, "pharmacyId": pharmacy_id, "patientSelectedPrice": price_quoted}
    if when:
        variables |= {"readyByDay": when.strftime("%A"), "readyByTime": when.isoformat()}
    await _mutate("setOrderPharmacy", queries.SET_ORDER_PHARMACY, "setOrderPharmacy",
                  variables, jwt, order_id, pharmacy_id)


async def wait_for_pharmacy(jwt: str, order_id: str, pharmacy_id: str, tries: int = 3) -> OrderStatus:
    """Re-read the order until it shows `pharmacy_id` (Photon updates a moment later)."""
    status = await order_status(jwt, order_id)
    for _ in range(tries - 1):
        if status.pharmacy and status.pharmacy.id == pharmacy_id:
            break
        await asyncio.sleep(1)
        status = await order_status(jwt, order_id)
    return status


async def set_preferred(jwt: str, patient_id: str, pharmacy_id: str, order_id: str) -> None:
    await _mutate("setPreferredPharmacy", queries.SET_PREFERRED_PHARMACY, "setPreferredPharmacy", {
        "patientId": patient_id, "pharmacyId": pharmacy_id,
    }, jwt, order_id, pharmacy_id)
