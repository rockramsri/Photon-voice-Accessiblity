"""Photon GraphQL transport — the one place that POSTs to Neutron APIs.

Three helpers, one per auth style:
  - patient_api(query, vars, jwt)   -> header x-photon-auth: <patient JWT>
  - clinical_api(query, vars, token)-> header x-photon-auth-token: <M2M token>
  - provider_api(query, vars, token)-> header Authorization: Bearer <token>

Everything above this layer deals in plain dicts; only this module knows the
headers, endpoints, Brotli workaround, and timeouts.
"""

from __future__ import annotations

import logging

import aiohttp

from shared import config

log = logging.getLogger("photon.client")

# Some aiohttp builds can't decode Brotli; ask servers for gzip/deflate only.
_BASE_HEADERS = {"content-type": "application/json", "accept-encoding": "gzip, deflate"}


async def _post(url: str, headers: dict, query: str, variables: dict | None, timeout: float) -> dict:
    body = {"query": query, "variables": variables or {}}
    async with aiohttp.ClientSession() as http:
        async with http.post(
            url, json=body, headers={**_BASE_HEADERS, **headers},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            return await resp.json()


async def patient_api(query: str, variables: dict | None = None, *, jwt: str, timeout: float = 4.0) -> dict:
    """Call the Patient API with a per-order patient JWT."""
    return await _post(config.PATIENT_API_URL, {"x-photon-auth": jwt}, query, variables, timeout)


async def clinical_api(query: str, variables: dict | None = None, *, token: str, timeout: float = 6.0) -> dict:
    """Call the Clinical (provider) API with the M2M token."""
    headers = {"x-photon-auth-token": token, "x-photon-auth-token-type": "auth0"}
    return await _post(config.CLINICAL_API_URL, headers, query, variables, timeout)


async def provider_api(query: str, variables: dict | None = None, *, token: str, timeout: float = 4.0) -> dict:
    """Call api.neutron.health with a Bearer token (fallback path)."""
    return await _post(config.PROVIDER_API_URL, {"authorization": f"Bearer {token}"}, query, variables, timeout)
