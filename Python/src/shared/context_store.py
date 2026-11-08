"""context/<orderId>.json — written by the dispatcher, read by the agent worker.

Holds {promptBlock, seed, shapedAt}. Local files only work while the worker
runs on the same machine as the dispatcher; swap for a shared store when deployed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from shared import config


def save_context(order_id: str, prompt_block: str, seed: dict) -> None:
    config.CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"orderId": order_id, "promptBlock": prompt_block, "seed": seed,
           "shapedAt": datetime.now(timezone.utc).isoformat()}
    (config.CONTEXT_DIR / f"{order_id}.json").write_text(json.dumps(doc, indent=2))


def load_context(order_id: str) -> dict | None:
    path = config.CONTEXT_DIR / f"{order_id}.json"
    return json.loads(path.read_text()) if path.exists() else None
