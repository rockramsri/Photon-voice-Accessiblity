"""Persisted set of processed orderIds — webhooks redeliver, we dial once.

A JSON file is plenty at this scale. Reads/writes are tiny and synchronous;
the webhook path calls these rarely (once per order), so no lock is needed
for a single-process dev server.
"""

from __future__ import annotations

import json
import logging

from shared import config

log = logging.getLogger("photon.dedupe")


def _load() -> set[str]:
    try:
        return set(json.loads(config.STATE_FILE.read_text()))
    except FileNotFoundError:
        return set()
    except Exception as exc:  # noqa: BLE001 — a corrupt file must not wedge the service
        log.error("could not read dedupe state (%s); starting empty", exc)
        return set()


def seen(order_id: str) -> bool:
    """True if this order was already processed."""
    return order_id in _load()


def mark(order_id: str) -> None:
    """Record an order as processed (idempotent)."""
    ids = _load()
    if order_id in ids:
        return
    ids.add(order_id)
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(json.dumps(sorted(ids), indent=2))
