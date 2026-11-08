"""Turn a raw prefetch dict into a voice-ready prompt block.

Pure functions: no network, no env. The dispatcher writes the result to
context/<orderId>.json; the agent reads the prompt block into its instructions.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/New_York")

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def money_to_words(amount) -> str:
    """$3.76 -> 'three dollars seventy-six'. None -> 'an unknown price'."""
    if amount is None:
        return "an unknown price"
    dollars = int(amount)
    cents = int(round((float(amount) - dollars) * 100))
    if cents == 100:  # rounding edge
        dollars += 1
        cents = 0
    spoken = _under_thousand(dollars) + (" dollar" if dollars == 1 else " dollars")
    if cents:
        spoken += " " + _under_hundred(cents)
    return spoken


def _under_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    ten, one = divmod(n, 10)
    return _TENS[ten] if one == 0 else f"{_TENS[ten]}-{_ONES[one]}"


def _under_thousand(n: int) -> str:
    if n < 100:
        return _under_hundred(n)
    hundreds, rest = divmod(n, 100)
    head = f"{_ONES[hundreds]} hundred"
    return head if rest == 0 else f"{head} {_under_hundred(rest)}"


def number_to_words(n: int) -> str:
    if n < 1000:
        return _under_thousand(n)
    thousands, rest = divmod(n, 1000)
    head = f"{_under_thousand(thousands)} thousand"
    return head if rest == 0 else f"{head} {_under_thousand(rest)}"


def words_for_numbers(text: str) -> str:
    """Spell out whole numbers so TTS says 'one thousand', not '1000'. Leaves decimals."""
    return re.sub(r"(?<![\d.])\d+(?![\d.])", lambda m: number_to_words(int(m.group())), text)


def ordinal_word(n: int) -> str:
    return _under_hundred(n)


def spaced_digits(digits: str) -> str:
    """'5138663212' -> 'five one three, eight six six, three two one two'."""
    d = re.sub(r"\D", "", digits)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    groups = [d[:3], d[3:6], d[6:]] if len(d) == 10 else [d]
    return ", ".join(" ".join(_ONES[int(c)] for c in g) for g in groups)


_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://([^\s/)]+)\S*")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)")
_MONEY = re.compile(r"\$(\d+(?:\.\d{1,2})?)")
_MARKUP = re.compile(r"\*+|`+|^#+\s*|^\s*(?:[-•]|\d+\.)\s+", re.MULTILINE)


def sanitize_for_voice(text: str) -> str:
    """Make any text safe to speak: no markdown, URLs, raw phones or $ signs.

    Leaves <...> tags alone so expressive-mode markup passes through.
    """
    text = _MD_LINK.sub(r"\1", text or "")
    text = _URL.sub(lambda m: m.group(1).replace(".", " dot "), text)
    text = _PHONE.sub(lambda m: spaced_digits(m.group(0)), text)
    text = _MONEY.sub(lambda m: money_to_words(float(m.group(1))), text)
    text = _MARKUP.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def spoken_strength(name: str) -> str:
    """'100 MG/5ML' -> '100 milligrams per 5 milliliters'."""
    name = re.sub(r"(\d+(?:\.\d+)?)\s*MG\s*/\s*(\d+(?:\.\d+)?)\s*ML\b",
                  r"\1 milligrams per \2 milliliters", name, flags=re.IGNORECASE)
    name = re.sub(r"(\d+(?:\.\d+)?)\s*MCG\b", r"\1 micrograms", name, flags=re.IGNORECASE)
    return re.sub(r"(\d+(?:\.\d+)?)\s*MG\b", r"\1 milligrams", name, flags=re.IGNORECASE)


_FORMS = ("tablet", "capsule", "suspension", "solution", "syrup", "cream", "ointment",
          "injection", "inhaler", "patch", "drops")
_STRENGTH = re.compile(r"\d+(?:\.\d+)? (?:milligrams|micrograms)(?: per \d+(?:\.\d+)? milliliters)?")


def spoken_med(name: str) -> tuple[str, str]:
    """Catalog name -> (full spoken name, short name).

    'metFORMIN HCl ER (MOD) Oral Tablet Extended Release 24 Hour 1000 MG'
    -> ('metformin extended-release, one thousand milligrams, tablets', 'metformin').
    Drops tall-man capitals, salts, routes and catalog codes that TTS reads badly.
    """
    text = spoken_strength(name or "").lower()
    words = re.findall(r"[a-z][a-z-]*", text)
    if not words:
        return "your prescription", "prescription"
    generic = words[0]
    full = generic + (" extended-release" if re.search(r"extended release|\ber\b|\bxr\b", text) else "")
    parts = [full]
    strength = _STRENGTH.search(text)
    if strength:
        parts.append(strength.group(0))
    form = next((f for f in _FORMS if f in text), None)
    if form:
        parts.append(form + "s" if form in ("tablet", "capsule") else form)
    return words_for_numbers(", ".join(parts)), generic


def dedupe_meds(fills: list | None) -> list[tuple[str, str]]:
    """One (full, short) spoken name per prescription (Photon repeats a fill per refill)."""
    seen: set[str] = set()
    meds: list[tuple[str, str]] = []
    for fill in fills or []:
        rx = (fill or {}).get("prescription") or {}
        rx_id = rx.get("id") or id(fill)
        if rx_id in seen:
            continue
        seen.add(rx_id)
        meds.append(spoken_med(((fill or {}).get("treatment") or {}).get("name")))
    return meds


def humanize_routing(order: dict | None) -> str:
    """Speak the LAST routing decision, or say none is chosen."""
    history = ((order or {}).get("metadata") or {}).get("routingHistory") or []
    if not history:
        return "no pharmacy chosen yet"
    last = history[-1]
    name = ((last.get("pharmacy") or {}).get("name") or "").strip()
    if not name:
        return "no pharmacy chosen yet"
    if last.get("selector") == "AUTO" and last.get("reason") == "SYSTEM_ROUTED_TO_OPEN_ORDER_PHARMACY":
        return f"currently headed to {name} because you have another prescription there"
    return f"currently headed to {name}"


def can_offer_change(order: dict | None) -> bool:
    ux = ((((order or {}).get("organization") or {}).get("settings") or {}).get("patientUx") or {})
    return bool((order or {}).get("isReroutable") and ux.get("enablePatientRerouting"))


def _is_24hr(place: dict) -> bool:
    return (((place.get("nextEvents") or {}).get("close")) or {}).get("type") in ("24hr", "open24hr")


def _event_time(event: dict | None) -> datetime | None:
    try:
        return datetime.fromisoformat(event["datetime"].replace("Z", "+00:00")).astimezone(_TZ)
    except (KeyError, TypeError, AttributeError, ValueError):
        return None


def spoken_hours(place: dict) -> str:
    """'open until five tonight', 'closed now, opens nine in the morning', 'open 24 hours'."""
    if _is_24hr(place):
        return "open 24 hours"
    events = place.get("nextEvents") or {}
    if place.get("isOpen") is False:
        when = _event_time(events.get("open"))
        return f"closed now, opens {spoken_clock(when)}" if when else "closed now"
    when = _event_time(events.get("close"))
    return f"open until {spoken_clock(when)}" if when else "hours unknown"


def spoken_clock(when: datetime) -> str:
    """15:00 -> 'three in the afternoon', 21:30 -> 'nine thirty tonight'."""
    hour12 = when.hour % 12 or 12
    clock = _under_hundred(hour12)
    if when.minute:
        clock += (" oh " if when.minute < 10 else " ") + _under_hundred(when.minute)
    if when.hour < 12:
        part = "in the morning"
    elif when.hour < 17:
        part = "in the afternoon"
    else:
        part = "tonight"
    return f"{clock} {part}"


def spoken_distance(miles: float | None) -> str:
    if miles is None:
        return "nearby"
    if miles < 0.3:
        return "a few blocks away"
    return f"about {miles:.1f} miles"


def shape_offers(bundles: list | None) -> list[str]:
    lines: list[str] = []
    for bundle in (bundles or [])[:3]:
        pharmacy = ((bundle.get("pharmacy") or {}).get("name") or "a pharmacy").strip()
        offer = (bundle.get("offers") or [{}])[0]
        treatment = (((offer.get("prescription") or {}).get("treatment") or {}).get("name")) or "the medication"
        price = (offer.get("prescriptionPrice") or {})
        promise = ((offer.get("deliveryEstimate") or {}).get("deliveryPromise")) or "delivery time unknown"
        line = (f"{pharmacy}: {treatment} about {money_to_words(price.get('amount'))} "
                f"(retail {money_to_words(price.get('retailAmount'))}), {promise}")
        tags = [t.get("kind") for t in (bundle.get("attributeTags") or [])]
        if bundle.get("isPromoted") or "SPONSORED" in tags:
            line += " — this is a sponsored option"
        lines.append(line)
    return lines


CHEAP_RADIUS_MI = 2.0


def rank_nearby(places: list | None) -> list[dict]:
    """Balanced picks first, then everything else closest-first.

    Picks (each tagged with a spoken "why"): closest open now, cheapest open
    within 2 miles, open latest. Photon can't filter open-now or sort by
    price, so it happens here. The prompt and the session state share this order.
    """
    by_distance = sorted(places or [], key=lambda p: p.get("distance") if p.get("distance") is not None else 1e9)
    open_now = [p for p in by_distance if p.get("isOpen")]
    cheap = [p for p in open_now if p.get("price") is not None and (p.get("distance") or 0) <= CHEAP_RADIUS_MI]

    def closes_at(p: dict) -> float:
        when = _event_time(((p.get("nextEvents") or {}).get("close")))
        return float("inf") if _is_24hr(p) else (when.timestamp() if when else 0.0)

    picked: list[dict] = []
    for place, label in (
        (next(iter(open_now), None), "closest"),
        (min(cheap, key=lambda p: p["price"], default=None), "cheapest"),
        (max(open_now, key=closes_at, default=None), "latest-closing"),
    ):
        if not place:
            continue
        same = next((p for p in picked if p.get("id") == place.get("id")), None)
        if same:
            same["labels"].append(label)
        else:
            picked.append({**place, "labels": [label]})
    for p in picked:
        labels = p.pop("labels")
        p["why"] = ("the only one open right now" if len(open_now) == 1
                    else "the " + " and ".join(labels) + " of the ones open now")
    ids = {p.get("id") for p in picked}
    return picked + [p for p in by_distance if p.get("id") not in ids]


_STREET_WORDS = {"ST": "Street", "AVE": "Avenue", "AV": "Avenue", "PKWY": "Parkway", "HWY": "Highway",
                 "BLVD": "Boulevard", "RD": "Road", "DR": "Drive", "PL": "Place", "LN": "Lane",
                 "W": "West", "E": "East", "N": "North", "S": "South", "STE": "Suite"}


def spoken_street(street1: str | None) -> str | None:
    """'1601 KINGS HWY' -> '1601 Kings Highway' so same-name stores sound different."""
    if not street1:
        return None
    return " ".join(_STREET_WORDS.get(w.upper().strip(","), w.capitalize()) for w in street1.split())


def nearby_line(place: dict) -> str:
    name = (place.get("name") or "a pharmacy").strip()
    street = spoken_street((place.get("address") or {}).get("street1"))
    why = f" ({place['why']})" if place.get("why") else ""
    line = f"{name}{f' on {street}' if street else ''}{why}, {spoken_distance(place.get('distance'))}, "
    line += spoken_hours(place)
    price, retail = place.get("price"), place.get("retailPrice")
    if price is not None:
        line += f", around {money_to_words(price)} with a discount card"
        if retail and retail > price:
            line += f" (retail {money_to_words(retail)})"
    return line


def shape_nearby(places: list | None) -> list[str]:
    return [f"Option {ordinal_word(i)}: {nearby_line(p)}"
            for i, p in enumerate(rank_nearby(places)[:3], start=1)]


def _mark(ok: bool, empty: bool = False) -> str:
    if ok and empty:
        return "✗(empty)"
    return "✓" if ok else "✗"


def build_prompt_block(ctx: dict) -> str:
    """Assemble the full block the agent reads. No raw JSON."""
    order = ctx.get("order") or {}
    org = ((order.get("organization") or {}).get("name")) or "the pharmacy"

    meds = dedupe_meds(order.get("fills"))
    if meds:
        meds_text = "; ".join(f"{full} (after the first mention say 'your {short}')" for full, short in meds)
    else:
        meds_text = "details unavailable, say 'your prescription'"

    offers = shape_offers(ctx.get("offers"))
    nearby = shape_nearby(ctx.get("nearby"))
    faqs = ctx.get("faqs") or []

    lines = [
        f"Organization: {org}.",
        f"Medication, say the full name once, slowly: {meds_text}.",
        f"Routing: {humanize_routing(order)}.",
        "Two ways to get it: pick it up at a nearby pharmacy, or have it mailed home by a mail-order pharmacy.",
    ]
    pharmacy = order.get("pharmacy") or {}
    if pharmacy.get("name"):
        lines.append(f"Current pharmacy: {pharmacy['name'].strip()} ({(order.get('fulfillment') or {}).get('type') or 'unknown'}).")

    if can_offer_change(order):
        lines.append("You MAY discuss switching pharmacies if the caller asks.")
    else:
        lines.append("Do NOT offer a pharmacy change. If asked, explain it can't be changed on this call and give the support path.")
    if ((order.get("fulfillment") or {}).get("state")) == "SENT":
        lines.append("Any change would be a transfer request, confirmed later by text.")

    lines.append("Marketplace offers: " + ("; ".join(offers) if offers else "none right now."))
    lines.append("Nearby pickup: " + ("; ".join(nearby) if nearby else "none available."))

    mail = [p["name"].strip() for p in (ctx.get("pharmacies") or []) if p.get("name")]
    if mail:
        lines.append("Mail delivery pharmacies (mailed to their home; offer ONE at a time by name, "
                     "never list several in one breath): " + ", ".join(mail) + ".")
    else:
        lines.append("Mail delivery: none available right now.")

    if faqs:
        lines.append("You may be asked:")
        for faq in faqs:
            lines.append(f"Q: {sanitize_for_voice(faq.get('question'))}")
            lines.append(f"A: {sanitize_for_voice(faq.get('answer'))}")

    comp = ctx.get("completeness") or {}
    offers_empty = ctx.get("offers") == []
    lines.append(
        "context: "
        f"order {_mark(comp.get('order', bool(order)))} "
        f"offers {_mark(comp.get('offers', ctx.get('offers') is not None), empty=offers_empty)} "
        f"nearby {_mark(comp.get('nearby', bool(nearby)))} "
        f"faqs {_mark(comp.get('faqs', bool(faqs)))}"
    )
    if ctx.get("partial"):
        lines.append("NOTE: this context is partial — some lookups failed. Do not invent the missing parts.")
    return "\n".join(lines)


def build_seed(ctx: dict, order_id: str, point: tuple[float, float] | None,
               patient: dict, patient_id: str) -> dict:
    """Machine-side twin of the prompt: ids + numbers for the session state.

    Never shown to the model. Candidates use the same ranking as the prompt so
    "option one" means the same pharmacy on both sides.
    """
    order = ctx.get("order") or {}
    fulfillment = order.get("fulfillment") or {}
    current = order.get("pharmacy") or {}

    candidates: list[dict] = []
    nearby = rank_nearby(ctx.get("nearby"))
    for i, place in enumerate(nearby):
        candidates.append(candidate_from_place(place, quoted=i < 3))
    for bundle in ctx.get("offers") or []:
        pharmacy = bundle.get("pharmacy") or {}
        if not pharmacy.get("id"):
            continue
        price = ((bundle.get("offers") or [{}])[0].get("prescriptionPrice")) or {}
        tags = [t.get("kind") for t in (bundle.get("attributeTags") or [])]
        candidates.append({
            "pharmacy_id": pharmacy["id"], "name": (pharmacy.get("name") or "").strip(),
            "source": "offer", "price": price.get("amount"), "retail": price.get("retailAmount"),
            "sponsored": bool(bundle.get("isPromoted") or "SPONSORED" in tags), "price_quoted": True,
        })
    for p in ctx.get("pharmacies") or []:
        if p.get("id"):
            candidates.append({"pharmacy_id": p["id"], "name": (p.get("name") or "").strip(),
                               "source": "mailorder"})
    if current.get("id"):
        candidates.append({"pharmacy_id": current["id"], "name": (current.get("name") or "").strip(),
                           "source": "current", "street": spoken_street((current.get("address") or {}).get("street1"))})

    return {
        "orderId": order_id,
        "patientId": patient_id,
        "patientFirstName": patient.get("first"),
        "patientPhone": patient.get("phone"),
        "dob": patient.get("dob"),
        "orgName": ((order.get("organization") or {}).get("name")),
        "canOfferChange": can_offer_change(order),
        "fulfillmentState": fulfillment.get("state"),
        "fulfillmentType": fulfillment.get("type"),
        "currentPharmacyId": current.get("id"),
        "medications": [full for full, _ in dedupe_meds(order.get("fills"))],
        "medShort": " and ".join(short for _, short in dedupe_meds(order.get("fills"))) or "prescription",
        "point": list(point) if point else None,
        "lastOptions": [p["id"] for p in nearby[:3] if p.get("id")],
        "candidates": candidates,
    }


def candidate_from_place(place: dict, quoted: bool) -> dict:
    """pharmaciesByLocation row -> Candidate fields."""
    return {
        "pharmacy_id": place.get("id"),
        "name": (place.get("name") or "").strip(),
        "source": "nearby",
        "price": place.get("price"),
        "retail": place.get("retailPrice"),
        "close_spoken": spoken_hours(place),
        "distance_spoken": spoken_distance(place.get("distance")),
        "street": spoken_street((place.get("address") or {}).get("street1")),
        "price_quoted": quoted and place.get("price") is not None,
    }
