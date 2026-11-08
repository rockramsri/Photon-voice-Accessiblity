"""Photon machine-to-machine (M2M) token — mint once, cache, rotate on 401.

One org-wide provider token from the OAuth client_credentials grant. It lasts
~24h, so we keep it in memory and only refetch when it is near expiry or when a
caller tells us a request got a 401 (call get_m2m_token(force_refresh=True)).

SECURITY: this token is powerful (org-wide). NEVER put it in room/agent
metadata. Only the per-order patient JWT (patient_token.py) goes to the agent.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from shared import config

log = logging.getLogger("auth.photon")

# In-memory cache. One backend process -> one shared token is fine.
_token: str = ""
_expires_at: float = 0.0
_lock = asyncio.Lock()


async def get_m2m_token(force_refresh: bool = False) -> str:
    """Return a valid M2M access token, minting a new one when needed."""
    global _token, _expires_at
    async with _lock:
        now = time.time()
        # Reuse the cached token until 60s before it expires.
        if not force_refresh and _token and now < _expires_at - 60:
            return _token
        _token, _expires_at = await _fetch_token()
        return _token


async def _fetch_token() -> tuple[str, float]:
    """Do the client_credentials exchange with Photon's auth server."""
    payload = {
        "client_id": config.PHOTON_CLIENT_ID,
        "client_secret": config.PHOTON_CLIENT_SECRET,
        "audience": config.PHOTON_AUDIENCE,
        "grant_type": "client_credentials",
    }
    # Ask for gzip/deflate only: some aiohttp builds can't decode Brotli ("br").
    headers = {"accept-encoding": "gzip, deflate"}
    async with aiohttp.ClientSession() as http:
        async with http.post(config.PHOTON_AUTH_URL, json=payload, headers=headers) as resp:
            data = await resp.json()

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"M2M token request failed: {data}")
    expires_in = float(data.get("expires_in", 3600))
    log.info("minted M2M token (valid ~%.0f min)", expires_in / 60)
    return token, time.time() + expires_in
