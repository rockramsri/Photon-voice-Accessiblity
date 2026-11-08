"""Per-call state, stored as AgentSession userdata.

Holds every pharmacy heard on this call (keyed by pharmacy id) and turns what
the caller says ("the second one", "Avenue O", "that CVS") into an id the
Photon mutations need. Ids never leave this object: tools speak names only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from rapidfuzz import fuzz

FUZZY_MIN = 85
_FILLER = {"the", "a", "an", "pharmacy", "drugs", "drug", "store", "option", "one", "at", "on"}
_ORDINALS = {
    "first": 1, "one": 1, "1": 1, "second": 2, "two": 2, "2": 2,
    "third": 3, "three": 3, "3": 3, "fourth": 4, "four": 4, "4": 4,
    "fifth": 5, "five": 5, "5": 5, "last": -1,
}


class Candidate(BaseModel):
    pharmacy_id: str
    name: str
    source: Literal["nearby", "offer", "mailorder", "current"]
    price: float | None = None
    retail: float | None = None
    close_spoken: str | None = None
    distance_spoken: str | None = None
    street: str | None = None
    sponsored: bool = False
    price_quoted: bool = False


class ResolveError(Exception):
    """Message is an instruction for the model (what to ask the caller)."""


@dataclass
class SessionState:
    order_id: str
    patient_id: str
    patient_token: str
    org_name: str
    first_name: str | None = None
    phone: str | None = None
    dob: str | None = None
    can_offer_change: bool = False
    fulfillment_state: str | None = None
    fulfillment_type: str | None = None
    current_pharmacy_id: str | None = None
    medications: list[str] = field(default_factory=list)
    med_short: str = "prescription"
    point: tuple[float, float] | None = None
    # search_results holds one ranked page; search_sig/cursor serve 3 at a time from it.
    search_results: list = field(default_factory=list)
    search_sig: tuple | None = None
    search_cursor: int = 0
    candidates: dict[str, Candidate] = field(default_factory=dict)
    last_options: list[str] = field(default_factory=list)
    identity_verified: bool = False
    dob_attempts: int = 0
    wrap_up_offered: bool = False
    end_reason: str | None = None

    @classmethod
    def from_seed(cls, seed: dict, patient_token: str, org_fallback: str) -> SessionState:
        state = cls(
            order_id=seed["orderId"],
            patient_id=seed["patientId"],
            patient_token=patient_token,
            org_name=seed.get("orgName") or org_fallback,
            first_name=seed.get("patientFirstName"),
            phone=seed.get("patientPhone"),
            dob=seed.get("dob"),
            can_offer_change=bool(seed.get("canOfferChange")),
            fulfillment_state=seed.get("fulfillmentState"),
            fulfillment_type=seed.get("fulfillmentType"),
            current_pharmacy_id=seed.get("currentPharmacyId"),
            medications=seed.get("medications") or [],
            med_short=seed.get("medShort") or "prescription",
            point=tuple(seed["point"]) if seed.get("point") else None,
        )
        state.add([Candidate(**c) for c in seed.get("candidates") or []])
        state.last_options = list(seed.get("lastOptions") or [])
        return state

    @property
    def current(self) -> Candidate | None:
        return self.candidates.get(self.current_pharmacy_id or "")

    def add(self, found: list[Candidate], spoken: list[str] | None = None) -> None:
        """Merge new pharmacies in. `spoken` = ids just read aloud, in order."""
        for c in found:
            old = self.candidates.get(c.pharmacy_id)
            if old and old.source == "current":
                c.source = "current"
            self.candidates[c.pharmacy_id] = c
        if spoken is not None:
            self.last_options = spoken

    def resolve(self, ref: str) -> Candidate:
        """Exact name -> ordinal of the last options -> fuzzy name. Raises ResolveError."""
        ref_norm = _norm(ref)
        exact = [c for c in self.candidates.values() if _norm(c.name) == ref_norm and ref_norm]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise ResolveError(_ambiguous(exact))

        by_ordinal = self._by_ordinal(ref)
        if by_ordinal:
            return by_ordinal

        scored = sorted(
            ((fuzz.token_set_ratio(ref_norm, _norm(c.name)), c) for c in self.candidates.values()),
            key=lambda pair: pair[0], reverse=True,
        )
        hits = [c for score, c in scored if score >= FUZZY_MIN and score >= scored[0][0] - 3] if scored else []
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ResolveError(_ambiguous(hits))
        raise ResolveError(
            f"Couldn't find a pharmacy matching '{ref}'. Ask the caller to say the name again, "
            "or offer to search nearby."
        )

    def _by_ordinal(self, ref: str) -> Candidate | None:
        words = re.findall(r"[a-z0-9]+", ref.lower())
        if not self.last_options or len(words) > 4:
            return None
        for word in words:
            n = _ORDINALS.get(word)
            if n is None:
                continue
            idx = len(self.last_options) - 1 if n == -1 else n - 1
            if 0 <= idx < len(self.last_options):
                return self.candidates.get(self.last_options[idx])
        return None


def _norm(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return " ".join(w for w in words if w not in _FILLER)


def _ambiguous(cands: list[Candidate]) -> str:
    def label(c: Candidate) -> str:
        detail = c.street or c.distance_spoken
        return f"{c.name} on {detail}" if c.street else (f"{c.name}, {detail}" if detail else c.name)
    names = " or ".join(label(c) for c in cands[:3])
    return f"More than one pharmacy matches. Ask whether they meant {names}."
