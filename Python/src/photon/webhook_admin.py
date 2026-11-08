"""Webhook config admin on the Clinical API.

list / create / delete the Photon webhook that points at our server. Used by
scripts/dev_tunnel.py to reset the webhook whenever the ngrok URL changes.

Auth: webhook management needs webhook_config permission, which the M2M app
token does NOT have by default. So we prefer config.PHOTON_TOKEN (a user token)
when set, and fall back to the M2M token (works only if the M2M app has been
granted webhook scopes in Photon settings).
"""

from __future__ import annotations

import logging

import aiohttp

from auth.photon_auth import get_m2m_token
from shared import config

log = logging.getLogger("photon.webhook_admin")

# Our webhook's name; also how dev_tunnel finds old ones to delete.
NAME = "voice-agent-dev"

FILTERS = [
    "photon:order:created",
    "photon:order:placed",
    "photon:order:rerouted",
    "photon:order:fulfillment",
    "photon:order:completed",
    "photon:order:canceled",
]


async def _auth_token() -> str:
    """User token if provided (has webhook perms), else the M2M token."""
    return config.PHOTON_TOKEN or await get_m2m_token()


async def _gql(query: str, variables: dict | None = None) -> dict:
    token = await _auth_token()
    headers = {
        "content-type": "application/json",
        "accept-encoding": "gzip, deflate",  # avoid Brotli (some aiohttp builds can't decode it)
        "x-photon-auth-token": token,
        "x-photon-auth-token-type": "auth0",
    }
    body = {"query": query, "variables": variables or {}}
    async with aiohttp.ClientSession() as http:
        async with http.post(config.CLINICAL_API_URL, json=body, headers=headers) as resp:
            return await resp.json()


async def list_webhooks() -> list[dict]:
    """Return all webhook configs on the org."""
    data = await _gql("{ webhooks { id name url filters } }")
    if data.get("errors"):
        log.error("list webhooks errors: %s", data["errors"])
        return []
    return (data.get("data") or {}).get("webhooks") or []


async def create_webhook(url: str) -> str | None:
    """Register our webhook at `url`; returns the new webhook id or None."""
    query = (
        "mutation ($name: String, $url: String!, $filters: [String!]!, $sharedSecret: String!) {"
        "  createWebhookConfig(name: $name, url: $url, filters: $filters, sharedSecret: $sharedSecret)"
        "}"
    )
    data = await _gql(query, {
        "name": NAME,
        "url": url,
        "filters": FILTERS,
        "sharedSecret": config.SHARED_SECRET,
    })
    if data.get("errors"):
        log.error("createWebhookConfig errors: %s", data["errors"])
        return None
    return (data.get("data") or {}).get("createWebhookConfig")


async def delete_webhook(webhook_id: str) -> None:
    """Delete one webhook config by id."""
    data = await _gql("mutation ($id: ID!) { deleteWebhookConfig(webhookId: $id) }", {"id": webhook_id})
    if data.get("errors"):
        log.error("deleteWebhookConfig errors for %s: %s", webhook_id, data["errors"])
