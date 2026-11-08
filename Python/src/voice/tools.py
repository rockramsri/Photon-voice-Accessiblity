"""The agent's tools. Every return value is a short instruction for the model,
written in speakable words — never JSON, never an id.

READ_TOOLS are always available. WRITE_TOOLS are only registered when the
order can still change pharmacy (see order_agent.build_order_agent).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from livekit.agents import RunContext, StopResponse, function_tool

from photon import actions
from photon.geocode import geocode_text
from shared import config
from voice.session_state import Candidate, ResolveError, SessionState
from voice.shape_context import (candidate_from_place, money_to_words, nearby_line, ordinal_word,
                                 rank_nearby, sanitize_for_voice, spaced_digits, spoken_clock)
from voice.spoken_time import parse_pickup

log = logging.getLogger("photon.tools")

TIMEOUT_S = 6.0
SEARCH_TIMEOUT_S = 10.0  # one wide page with prices runs ~2–5s
SEARCH_FILLER = '<expr type="expression" label="curious"/> Hmm, one sec, let me pull up the pharmacies near you.'
COMMIT_FILLER = '<expr type="expression" label="confident"/> Okay, locking that in now, one sec.'
FAILED = ("The change didn't go through. Tell the caller you couldn't finish it on this call "
          "and they'll get a text link to finish it instead.")
# Said when the change is in but Photon hasn't confirmed yet — no server-side tracking.
PENDING = ("Change submitted but not confirmed yet. Tell the caller: you'll get a text in a few "
           "seconds confirming it; if not, call {support} or I'll call you back. Do NOT say it's done.")


def _say(text: str) -> str:
    return sanitize_for_voice(text)


def _readback(state: SessionState, c: Candidate) -> str:
    how = "mailed to you by" if c.source == "mailorder" else "for pickup at"
    price = f", around {money_to_words(c.price)} with a discount card" if c.price_quoted else ""
    sponsored = " Mention that it's a sponsored option." if c.sponsored else ""
    return f"your {state.med_short}, {how} {c.name}{price}.{sponsored}"


async def _commit(state: SessionState, c: Candidate, when=None) -> str | None:
    """Make the change, then re-read the order. 'verified' | 'transfer' | None (failed).

    A pharmacy-less order needs setOrderPharmacy; rerouteOrder only moves an
    order that already has one (on a new order it returns true and does nothing).
    """
    if config.DRY_RUN:
        log.info("DRY_RUN commit order=%s pharmacy=%s", state.order_id, c.pharmacy_id)
        state.current_pharmacy_id = c.pharmacy_id
        return "verified"
    jwt = state.patient_token
    try:
        async with asyncio.timeout(TIMEOUT_S):
            if when or not state.current_pharmacy_id:
                await actions.set_order_pharmacy(jwt, state.order_id, c.pharmacy_id, c.price_quoted, when)
            else:
                await actions.reroute(jwt, state.order_id, c.pharmacy_id, c.price_quoted)
            status = await actions.wait_for_pharmacy(jwt, state.order_id, c.pharmacy_id)
    except (TimeoutError, actions.PhotonError):
        return None

    if status.pharmacy and status.pharmacy.id == c.pharmacy_id:
        outcome = "verified"
    elif state.fulfillment_state == "SENT":
        outcome = "transfer"
    else:
        log.warning("order=%s pharmacy unchanged after commit to %s", state.order_id, c.pharmacy_id)
        return None
    state.current_pharmacy_id = c.pharmacy_id
    return outcome


def _gate(state: SessionState, ref: str) -> Candidate | str:
    """Shared checks for write tools: identity, then resolve. Returns Candidate or an instruction."""
    if not state.identity_verified:
        return "Confirm the caller's date of birth with verify_identity before making any change."
    try:
        return state.resolve(ref)
    except ResolveError as exc:
        return str(exc)


# ---------------------------------------------------------------- read tools

@function_tool
async def verify_identity(context: RunContext[SessionState], month: int, day: int, year: int) -> str:
    """Check the caller's date of birth. Call this right after they say it.

    Pass the parts exactly as the caller said them — don't reorder or guess.
    If any part is unclear, ask them to repeat it instead of calling this.

    Args:
        month: Month as a number, 1 to 12.
        day: Day of the month as a number, 1 to 31.
        year: Four-digit year, e.g. 2002. "two thousand" is 2000, "oh three" / "'03" is 2003.
    """
    state = context.userdata
    state.dob_attempts += 1
    if not state.dob:
        log.warning("order=%s has no DOB on file — cannot verify", state.order_id)
        return "You can't verify identity on this call. Apologize, say they'll get a text instead, then call end_call with reason done."
    try:
        y, m, d = (int(x) for x in state.dob[:10].split("-"))
    except ValueError:
        log.warning("order=%s has unparseable DOB on file", state.order_id)
        return "You can't verify identity on this call. Apologize, say they'll get a text instead, then call end_call with reason done."
    if (month, day, year) == (m, d, y):
        state.identity_verified = True
        reveal = getattr(context.session.current_agent, "reveal_context", None)
        if reveal:
            await reveal()  # only now do the order details enter the prompt
        return "Verified. Continue: say why you're calling in one line, then name the medication once."
    if state.dob_attempts < 3:
        return "That doesn't match. Ask them to say the month, day, and year again, slowly."
    return "Still no match. Apologize kindly, say you can't discuss the order, then call end_call with reason done."


@function_tool
async def search_nearby(
    context: RunContext[SessionState],
    area_hint: str | None = None,
    name_filter: str | None = None,
    open_24hr: bool = False,
    more: bool = False,
) -> str:
    """Find pickup pharmacies NOT already in your context. Returns three at a time.

    Args:
        area_hint: A street address the caller wants to search near, e.g. "350 5th Ave, New York, NY". Leave empty to search near their home.
        name_filter: A pharmacy name to look for, e.g. "Walgreens".
        open_24hr: Only pharmacies open 24 hours.
        more: True when the caller wants the next three from the same search.
    """
    state = context.userdata
    if area_hint:
        point = await geocode_text(area_hint)
        if not point:
            return "Couldn't find that location. Ask for a street address with the city."
        state.point = point
    if not state.point:
        return "There's no location to search from. Ask for a street address with the city."

    sig = (name_filter or "", bool(open_24hr), area_hint or "")
    if more and state.search_results and sig == state.search_sig:
        state.search_cursor += 3
    else:
        context.session.say(SEARCH_FILLER)  # fills the ~2–5s fetch; not awaited
        try:
            places = await asyncio.wait_for(actions.search_pharmacies(
                state.patient_token, *state.point, name=name_filter, is24hr=open_24hr), SEARCH_TIMEOUT_S)
        except (asyncio.TimeoutError, actions.PhotonError):
            return "The search didn't work right now. Tell the caller, and offer the options you already have."
        state.search_results = rank_nearby([p for p in places if p.get("id")])
        state.search_sig, state.search_cursor = sig, 0

    batch = state.search_results[state.search_cursor:state.search_cursor + 3]
    if not batch:
        return "That's all the pickup options I have there. Suggest mail delivery or one already mentioned."
    state.add([Candidate(**candidate_from_place(p, quoted=True)) for p in batch],
              spoken=[p["id"] for p in batch])
    lines = [f"Option {ordinal_word(i)}: {nearby_line(p)}."
             for i, p in enumerate(batch, start=state.search_cursor + 1)]
    return _say(" ".join(lines) + " Read ONE at a time; after each, ask if they want it or the next.")


# --------------------------------------------------------------- write tools

@function_tool
async def choose_pharmacy(context: RunContext[SessionState], candidate_ref: str, confirmed: bool) -> str:
    """Move the order to the pharmacy the caller picked.

    Args:
        candidate_ref: The pharmacy exactly as you listed it, or what the caller said ("option two").
        confirmed: True ONLY after you read back the medication and pharmacy and the caller clearly said yes.
    """
    state = context.userdata
    c = _gate(state, candidate_ref)
    if isinstance(c, str):
        return c
    if c.pharmacy_id == state.current_pharmacy_id:
        return f"The order is already going to {c.name}. Tell them nothing needs to change."
    if confirmed is not True:
        return _say(f"DO NOT COMMIT yet. Read back: {_readback(state, c)} Ask: shall I lock that in? "
                    "Call again with confirmed=true only after a clear yes.")

    context.session.say(COMMIT_FILLER)  # fills the write; not awaited
    outcome = await _commit(state, c)
    if outcome == "verified":
        return _say(f"Verified: the order now goes to {c.name}. Tell the caller it's done. "
                    "Then offer to make it their usual pharmacy.")
    if outcome == "transfer":
        return _say(PENDING.format(support=spaced_digits(config.SUPPORT_SMS_NUMBER)) + f" ({c.name})")
    return FAILED


@function_tool
async def set_pickup_time(context: RunContext[SessionState], candidate_ref: str, day: str,
                          time: str, confirmed: bool) -> str:
    """Set the pharmacy and when the caller wants to pick up.

    Args:
        candidate_ref: The pharmacy exactly as you listed it, or what the caller said.
        day: The day as the caller said it, e.g. "tomorrow", "friday".
        time: The time as the caller said it, e.g. "after four", "nine thirty in the morning".
        confirmed: True ONLY after you read back pharmacy, day and time and the caller said yes.
    """
    state = context.userdata
    c = _gate(state, candidate_ref)
    if isinstance(c, str):
        return c
    when = parse_pickup(day, time)
    if not when:
        return "That day or time wasn't clear or has already passed. Ask which day and roughly what time."
    spoken_when = f"{when.strftime('%A')} around {spoken_clock(when)}"
    if confirmed is not True:
        return _say(f"DO NOT COMMIT yet. Read back: {_readback(state, c)} Ready {spoken_when}. "
                    "Ask: shall I lock that in? Call again with confirmed=true only after a clear yes.")

    context.session.say(COMMIT_FILLER)  # fills the write; not awaited
    outcome = await _commit(state, c, when)
    if outcome == "verified":
        return _say(f"Verified: {c.name}, ready {spoken_when}. Tell the caller it's set.")
    if outcome == "transfer":
        return _say(PENDING.format(support=spaced_digits(config.SUPPORT_SMS_NUMBER)) + f" ({c.name}, {spoken_when})")
    return FAILED


@function_tool
async def save_preferred_pharmacy(context: RunContext[SessionState], candidate_ref: str) -> str:
    """Make a pharmacy the caller's usual pharmacy for future prescriptions.

    Args:
        candidate_ref: The pharmacy exactly as you listed it.
    """
    state = context.userdata
    c = _gate(state, candidate_ref)
    if isinstance(c, str):
        return c
    if config.DRY_RUN:
        return f"(dry run) Saved {c.name} as their usual pharmacy."
    try:
        await asyncio.wait_for(actions.set_preferred(
            state.patient_token, state.patient_id, c.pharmacy_id, state.order_id), TIMEOUT_S)
    except (asyncio.TimeoutError, actions.PhotonError):
        return "Couldn't save it right now. Tell them they can set it from the text link."
    return f"Saved {c.name} as their usual pharmacy. Acknowledge briefly."


# ------------------------------------------------------------------ end call

@function_tool
async def end_call(context: RunContext[SessionState],
                   reason: Literal["human_requested", "clinical", "distress", "done"]) -> str:
    """End the call. Use when the caller is done, asks for a person, is upset, or asks a medical question you can't answer.

    Args:
        reason: Why the call is ending. "done" only after they confirm there's nothing else.
    """
    state = context.userdata
    # For "done", make sure we've offered to help with anything else before hanging up.
    if reason == "done" and not state.wrap_up_offered:
        state.wrap_up_offered = True
        return "Ask: is there anything else I can help with? Call end_call again only on a clear no."

    state.end_reason = reason
    log.info("end_call order=%s reason=%s", state.order_id, reason)
    support = spaced_digits(config.SUPPORT_SMS_NUMBER)
    name = state.first_name or "there"
    if reason == "clinical":
        goodbye = f'<expr type="expression" label="empathetic"/> That\'s a question for your doctor\'s office, {name}. You can also text support at {support}. Take care now, goodbye.'
    elif reason in ("human_requested", "distress"):
        goodbye = f'<expr type="expression" label="empathetic"/> A real person can help with that — text Photon support at {support}. <expr type="prosody" label="soft">Take care, {name}. Goodbye.</expr>'
    else:
        goodbye = f'<expr type="expression" label="grateful"/> Thanks for your time, {name}. Take care, goodbye.'
    # Speak the goodbye ourselves and shut down only once it has finished playing.
    handle = context.session.say(goodbye)
    handle.add_done_callback(lambda _: context.session.shutdown())
    raise StopResponse


READ_TOOLS = [verify_identity, search_nearby, end_call]
WRITE_TOOLS = [choose_pharmacy, set_pickup_time, save_preferred_pharmacy]
