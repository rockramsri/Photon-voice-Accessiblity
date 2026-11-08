"""CloudEvents parsing + Photon webhook signature verification.

Photon delivers application/cloudevents-batch+json: the body may be a JSON
ARRAY of events OR a single event object — we normalise both to a list.

Signature verification is LOG-ONLY for now (we don't yet know which byte
layout Photon signs). We compute candidate HMACs over the raw body and over
`${timestamp}.${rawBody}` and log which one matches so we can lock it down
later. TODO: once the matching variant is confirmed, reject on mismatch.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

log = logging.getLogger("photon.events")

SIGNATURE_HEADER = "x-photon-signature"
TIMESTAMP_HEADER = "x-photon-timestamp"


def parse_events(raw: bytes) -> list[dict]:
    """Decode a CloudEvents batch body into a flat list of event dicts."""
    try:
        payload = json.loads(raw.decode() or "null")
    except Exception as exc:  # noqa: BLE001 — malformed body is not fatal here
        log.error("could not parse webhook body as JSON: %s", exc)
        return []
    if payload is None:
        return []
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        return [payload]
    log.warning("unexpected webhook payload type: %s", type(payload).__name__)
    return []


def verify_signature(raw: bytes, timestamp: str, signature: str, secret: str) -> dict:
    """LOG-ONLY HMAC check. Returns which candidate (if any) matched.

    TODO(security): enforce this — reject the webhook when no variant matches —
    once we have confirmed from real deliveries which layout Photon signs.
    """
    result = {"present": bool(signature), "matched": None}
    if not signature:
        log.warning("webhook has no %s header — cannot verify (log-only)", SIGNATURE_HEADER)
        return result
    if not secret:
        log.warning("SHARED_SECRET unset — skipping signature check (log-only)")
        return result

    candidates = {
        "raw": raw,
        "timestamp.raw": f"{timestamp}.".encode() + raw if timestamp else None,
    }
    for name, message in candidates.items():
        if message is None:
            continue
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            result["matched"] = name
            log.info("webhook signature OK — matched variant %r (log-only)", name)
            return result

    log.warning("webhook signature MISMATCH on all variants (log-only; not rejecting)")
    return result
