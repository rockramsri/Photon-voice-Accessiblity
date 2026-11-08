"""Per-order patient JWT — the token patient-api accepts via `x-photon-auth`.

Verified flow:
  1. createShortlink(type: MARKETPLACE) on the Clinical API (with the M2M token)
     returns an sl.neutron.health URL.
  2. That URL 302-redirects to orders.neutron.health with the JWT in the
     `?token=` query param.

The JWT is scoped to ONE order (even the same patient's other orders are
rejected), so we mint one per order. Because it can only touch this single
order, it is safe to hand to the voice agent for this one call.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

import aiohttp

from auth.photon_auth import get_m2m_token
from shared import config

log = logging.getLogger("auth.patient_token")

_CREATE_SHORTLINK = """
mutation ($input: ShortlinkInput!) {
  createShortlink(input: $input) { url shortcode }
}
"""


async def mint_patient_jwt(order_id: str, patient_id: str) -> str | None:
    """Return a one-order patient JWT for patient-api, or None on failure."""
    url = await _create_shortlink(order_id, patient_id)
    if not url:
        return None
    return await _token_from_shortlink(url)


async def _create_shortlink(order_id: str, patient_id: str, retry: bool = True) -> str | None:
    """Create a MARKETPLACE shortlink; refresh the M2M token once on auth error."""
    try:
        # First attempt uses the cached M2M token; the retry forces a fresh one.
        token = await get_m2m_token(force_refresh=not retry)
        headers = {
            "content-type": "application/json",
            "accept-encoding": "gzip, deflate",  # avoid Brotli (some aiohttp builds can't decode it)
            "x-photon-auth-token": token,
            "x-photon-auth-token-type": "auth0",
        }
        body = {
            "query": _CREATE_SHORTLINK,
            "variables": {"input": {
                "patientId": patient_id,
                "orderId": order_id,
                "type": "MARKETPLACE",
                "channelName": "voice-agent",
            }},
        }
        async with aiohttp.ClientSession() as http:
            async with http.post(config.CLINICAL_API_URL, json=body, headers=headers) as resp:
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001 — token fetch or request failure degrades gracefully
        log.error("createShortlink request failed for order %s: %s", order_id, exc)
        return None

    # Rotate the M2M token once if it was rejected, then retry.
    if retry and _is_auth_error(data):
        log.warning("createShortlink auth error — refreshing M2M token and retrying")
        return await _create_shortlink(order_id, patient_id, retry=False)

    link = (data.get("data") or {}).get("createShortlink")
    if not link:
        log.error("createShortlink failed for order %s: %s", order_id, data.get("errors"))
        return None
    log.info("created shortlink for order %s", order_id)
    return link["url"]


async def _token_from_shortlink(url: str) -> str | None:
    """GET the shortlink WITHOUT following redirects; read ?token= from Location."""
    try:
        headers = {"accept-encoding": "gzip, deflate"}
        async with aiohttp.ClientSession() as http:
            async with http.get(url, allow_redirects=False, headers=headers) as resp:
                location = resp.headers.get("Location", "")
    except Exception as exc:  # noqa: BLE001
        log.error("shortlink GET failed: %s", exc)
        return None

    token = parse_qs(urlparse(location).query).get("token", [None])[0]
    if not token:
        log.error("no token in shortlink redirect: %s", location[:120])
        return None
    return token


def _is_auth_error(data: dict) -> bool:
    """True if any GraphQL error looks like an auth/authz failure."""
    for err in data.get("errors") or []:
        code = (err.get("extensions") or {}).get("code", "")
        if "AUTH" in code.upper():
            return True
    return False
