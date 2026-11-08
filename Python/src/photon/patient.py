"""Patient lookup via the Patient API, using the order-scoped JWT.

Reads the patient's first name + phone from patient-api with header
`x-photon-auth: <jwt>`. The JWT is scoped to one order and its patient, so
`me` returns that patient. No clinical-api and no M2M token live here — this
module is patient-plane only.
"""

from __future__ import annotations

import logging

import aiohttp

from shared import config

log = logging.getLogger("photon.patient")

_QUERY = "{ me { name { first } phone dateOfBirth address { street1 city state postalCode } } }"
_EMPTY = {"first": None, "phone": None, "address": None, "dob": None}


async def fetch_patient(jwt: str) -> dict:
    """Return {"first", "phone", "address", "dob"} for the JWT's patient."""
    headers = {
        "content-type": "application/json",
        "accept-encoding": "gzip, deflate",  # avoid Brotli (some aiohttp builds can't decode it)
        "x-photon-auth": jwt,
    }
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(config.PATIENT_API_URL, json={"query": _QUERY}, headers=headers) as resp:
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001 — a lookup failure degrades gracefully
        log.error("patient lookup failed: %s", exc)
        return dict(_EMPTY)

    if data.get("errors"):
        log.error("patient lookup GraphQL errors: %s", data["errors"])
        return dict(_EMPTY)

    me = (data.get("data") or {}).get("me") or {}
    first = ((me.get("name") or {}).get("first")) or None
    phone = me.get("phone") or None
    address = me.get("address") or None
    dob = me.get("dateOfBirth") or None
    log.info("patient -> first=%s phone=%s address=%s dob=%s", first, bool(phone), bool(address), bool(dob))
    return {"first": first, "phone": phone, "address": address, "dob": dob}
