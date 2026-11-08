/**
 * The agent's tools. Every return value is a short instruction for the model,
 * written in speakable words — never JSON, never an id.
 * READ_TOOLS are always available; WRITE_TOOLS only when the order can change.
 */
import { llm, voice, type RunContext } from '@livekit/agents';
import { z } from 'zod';
import { config } from '../config.js';
import { getLogger } from '../log.js';
import * as actions from '../photon/actions.js';
import { geocodeText } from '../photon/geocode.js';
import type { Candidate, SessionState } from './sessionState.js';
import { ResolveError } from './sessionState.js';
import {
  candidateFromPlace, moneyToWords, nearbyLine, ordinalWord, rankNearby,
  sanitizeForVoice, spacedDigits, spokenClock,
} from './shapeContext.js';
import { parsePickup } from './spokenTime.js';

const log = getLogger('photon.tools');

const TIMEOUT_MS = 6000;
const SEARCH_TIMEOUT_MS = 10_000;
const SEARCH_FILLER = '<expr type="expression" label="curious"/> Hmm, one sec, let me pull up the pharmacies near you.';
const COMMIT_FILLER = '<expr type="expression" label="confident"/> Okay, locking that in now, one sec.';
const FAILED =
  "The change didn't go through. Tell the caller you couldn't finish it on this call " +
  "and they'll get a text link to finish it instead.";
const PENDING =
  'Change submitted but not confirmed yet. Tell the caller: you\'ll get a text in a few ' +
  'seconds confirming it; if not, call {support} or I\'ll call you back. Do NOT say it\'s done.';
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

type Ctx = RunContext<SessionState>;

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms);
    p.then((v) => { clearTimeout(timer); resolve(v); }, (e) => { clearTimeout(timer); reject(e); });
  });
}

function say(text: string): string {
  return sanitizeForVoice(text);
}

function readback(state: SessionState, c: Candidate): string {
  const how = c.source === 'mailorder' ? 'mailed to you by' : 'for pickup at';
  const price = c.price_quoted ? `, around ${moneyToWords(c.price)} with a discount card` : '';
  const sponsored = c.sponsored ? " Mention that it's a sponsored option." : '';
  return `your ${state.medShort}, ${how} ${c.name}${price}.${sponsored}`;
}

async function commit(state: SessionState, c: Candidate, when: Date | null = null): Promise<string | null> {
  if (config.DRY_RUN) {
    log.info(`DRY_RUN commit order=${state.orderId} pharmacy=${c.pharmacy_id}`);
    state.currentPharmacyId = c.pharmacy_id;
    return 'verified';
  }
  const jwt = state.patientToken;
  let status: actions.OrderStatus;
  try {
    await withTimeout(
      (when || !state.currentPharmacyId)
        ? actions.setOrderPharmacy(jwt, state.orderId, c.pharmacy_id, Boolean(c.price_quoted), when)
        : actions.reroute(jwt, state.orderId, c.pharmacy_id, Boolean(c.price_quoted)),
      TIMEOUT_MS,
    );
    status = await actions.waitForPharmacy(jwt, state.orderId, c.pharmacy_id);
  } catch {
    return null;
  }

  let outcome: string;
  if (status.pharmacy && status.pharmacy.id === c.pharmacy_id) outcome = 'verified';
  else if (state.fulfillmentState === 'SENT') outcome = 'transfer';
  else {
    log.warn(`order=${state.orderId} pharmacy unchanged after commit to ${c.pharmacy_id}`);
    return null;
  }
  state.currentPharmacyId = c.pharmacy_id;
  return outcome;
}

function gate(state: SessionState, ref: string): Candidate | string {
  if (!state.identityVerified) {
    return "Confirm the caller's date of birth with verify_identity before making any change.";
  }
  try {
    return state.resolve(ref);
  } catch (exc) {
    if (exc instanceof ResolveError) return exc.message;
    throw exc;
  }
}

// ---------------------------------------------------------------- read tools

export const verifyIdentity = llm.tool({
  name: 'verify_identity',
  description:
    "Check the caller's date of birth. Call this right after they say it. Pass the parts exactly " +
    "as the caller said them — don't reorder or guess. If any part is unclear, ask them to repeat it.",
  parameters: z.object({
    month: z.number().int().describe('Month as a number, 1 to 12.'),
    day: z.number().int().describe('Day of the month as a number, 1 to 31.'),
    year: z.number().int().describe('Four-digit year, e.g. 2002. "two thousand" is 2000, "oh three" is 2003.'),
  }),
  execute: async ({ month, day, year }, { ctx }: { ctx: Ctx }) => {
    const state = ctx.userData;
    state.dobAttempts += 1;
    if (!state.dob) {
      log.warn(`order=${state.orderId} has no DOB on file — cannot verify`);
      return "You can't verify identity on this call. Apologize, say they'll get a text instead, then call end_call with reason done.";
    }
    const parts = state.dob.slice(0, 10).split('-').map((x) => parseInt(x, 10));
    if (parts.some((n) => Number.isNaN(n))) {
      log.warn(`order=${state.orderId} has unparseable DOB on file`);
      return "You can't verify identity on this call. Apologize, say they'll get a text instead, then call end_call with reason done.";
    }
    const [y, m, d] = parts;
    if (month === m && day === d && year === y) {
      state.identityVerified = true;
      const agent = ctx.session.currentAgent as { revealContext?: () => Promise<void> };
      if (typeof agent.revealContext === 'function') await agent.revealContext();
      return 'Verified. Continue: say why you\'re calling in one line, then name the medication once.';
    }
    if (state.dobAttempts < 3) return "That doesn't match. Ask them to say the month, day, and year again, slowly.";
    return "Still no match. Apologize kindly, say you can't discuss the order, then call end_call with reason done.";
  },
});

export const searchNearby = llm.tool({
  name: 'search_nearby',
  description: 'Find pickup pharmacies NOT already in your context. Returns three at a time.',
  parameters: z.object({
    area_hint: z.string().nullish().describe('A street address to search near, e.g. "350 5th Ave, New York, NY". Empty = near their home.'),
    name_filter: z.string().nullish().describe('A pharmacy name to look for, e.g. "Walgreens".'),
    open_24hr: z.boolean().nullish().describe('Only pharmacies open 24 hours.'),
    more: z.boolean().nullish().describe('True when the caller wants the next three from the same search.'),
  }),
  execute: async ({ area_hint, name_filter, open_24hr, more }, { ctx }: { ctx: Ctx }) => {
    const state = ctx.userData;
    if (area_hint) {
      const point = await geocodeText(area_hint);
      if (!point) return 'Couldn\'t find that location. Ask for a street address with the city.';
      state.point = point;
    }
    if (!state.point) return "There's no location to search from. Ask for a street address with the city.";

    const sig = JSON.stringify([name_filter || '', Boolean(open_24hr), area_hint || '']);
    if (more && state.searchResults.length && sig === state.searchSig) {
      state.searchCursor += 3;
    } else {
      ctx.session.say(SEARCH_FILLER); // fills the ~2–5s fetch; not awaited
      let places;
      try {
        places = await withTimeout(
          actions.searchPharmacies(state.patientToken, state.point[0], state.point[1], { name: name_filter, is24hr: Boolean(open_24hr) }),
          SEARCH_TIMEOUT_MS,
        );
      } catch {
        return 'The search didn\'t work right now. Tell the caller, and offer the options you already have.';
      }
      state.searchResults = rankNearby(places.filter((p) => p.id));
      state.searchSig = sig;
      state.searchCursor = 0;
    }

    const batch = state.searchResults.slice(state.searchCursor, state.searchCursor + 3);
    if (!batch.length) return "That's all the pickup options I have there. Suggest mail delivery or one already mentioned.";
    state.add(batch.map((p) => candidateFromPlace(p, true) as Candidate), batch.map((p) => p.id));
    const lines = batch.map((p, i) => `Option ${ordinalWord(state.searchCursor + 1 + i)}: ${nearbyLine(p)}.`);
    return say(`${lines.join(' ')} Read ONE at a time; after each, ask if they want it or the next.`);
  },
});

// ------------------------------------------------------------------ end call

export const endCall = llm.tool({
  name: 'end_call',
  description:
    'End the call. Use when the caller is done, asks for a person, is upset, or asks a medical question you can\'t answer.',
  parameters: z.object({
    reason: z.enum(['human_requested', 'clinical', 'distress', 'done']).describe('Why the call is ending.'),
  }),
  execute: async ({ reason }, { ctx }: { ctx: Ctx }) => {
    const state = ctx.userData;
    if (reason === 'done' && !state.wrapUpOffered) {
      state.wrapUpOffered = true;
      return 'Ask: is there anything else I can help with? Call end_call again only on a clear no.';
    }
    state.endReason = reason;
    log.info(`end_call order=${state.orderId} reason=${reason}`);
    const support = spacedDigits(config.SUPPORT_SMS_NUMBER);
    const name = state.firstName || 'there';
    let goodbye: string;
    if (reason === 'clinical') {
      goodbye = `<expr type="expression" label="empathetic"/> That's a question for your doctor's office, ${name}. You can also text support at ${support}. Take care now, goodbye.`;
    } else if (reason === 'human_requested' || reason === 'distress') {
      goodbye = `<expr type="expression" label="empathetic"/> A real person can help with that — text Photon support at ${support}. <expr type="prosody" label="soft">Take care, ${name}. Goodbye.</expr>`;
    } else {
      goodbye = `<expr type="expression" label="grateful"/> Thanks for your time, ${name}. Take care, goodbye.`;
    }
    const handle = ctx.session.say(goodbye);
    handle.addDoneCallback(() => state.endCall());
    throw new voice.StopResponse();
  },
});

// --------------------------------------------------------------- write tools

export const choosePharmacy = llm.tool({
  name: 'choose_pharmacy',
  description: 'Move the order to the pharmacy the caller picked.',
  parameters: z.object({
    candidate_ref: z.string().describe('The pharmacy exactly as you listed it, or what the caller said ("option two").'),
    confirmed: z.boolean().describe('True ONLY after you read back medication and pharmacy and the caller clearly said yes.'),
  }),
  execute: async ({ candidate_ref, confirmed }, { ctx }: { ctx: Ctx }) => {
    const state = ctx.userData;
    const c = gate(state, candidate_ref);
    if (typeof c === 'string') return c;
    if (c.pharmacy_id === state.currentPharmacyId) {
      return `The order is already going to ${c.name}. Tell them nothing needs to change.`;
    }
    if (confirmed !== true) {
      return say(`DO NOT COMMIT yet. Read back: ${readback(state, c)} Ask: shall I lock that in? Call again with confirmed=true only after a clear yes.`);
    }
    ctx.session.say(COMMIT_FILLER);
    const outcome = await commit(state, c);
    if (outcome === 'verified') {
      return say(`Verified: the order now goes to ${c.name}. Tell the caller it's done. Then offer to make it their usual pharmacy.`);
    }
    if (outcome === 'transfer') return say(`${PENDING.replace('{support}', spacedDigits(config.SUPPORT_SMS_NUMBER))} (${c.name})`);
    return FAILED;
  },
});

export const setPickupTime = llm.tool({
  name: 'set_pickup_time',
  description: 'Set the pharmacy and when the caller wants to pick up.',
  parameters: z.object({
    candidate_ref: z.string().describe('The pharmacy exactly as you listed it, or what the caller said.'),
    day: z.string().describe('The day as the caller said it, e.g. "tomorrow", "friday".'),
    time: z.string().describe('The time as the caller said it, e.g. "after four", "nine thirty in the morning".'),
    confirmed: z.boolean().describe('True ONLY after you read back pharmacy, day and time and the caller said yes.'),
  }),
  execute: async ({ candidate_ref, day, time, confirmed }, { ctx }: { ctx: Ctx }) => {
    const state = ctx.userData;
    const c = gate(state, candidate_ref);
    if (typeof c === 'string') return c;
    const when = parsePickup(day, time);
    if (!when) return 'That day or time wasn\'t clear or has already passed. Ask which day and roughly what time.';
    const spokenWhen = `${WEEKDAYS[when.getUTCDay()]} around ${spokenClock(when)}`;
    if (confirmed !== true) {
      return say(`DO NOT COMMIT yet. Read back: ${readback(state, c)} Ready ${spokenWhen}. Ask: shall I lock that in? Call again with confirmed=true only after a clear yes.`);
    }
    ctx.session.say(COMMIT_FILLER);
    const outcome = await commit(state, c, when);
    if (outcome === 'verified') return say(`Verified: ${c.name}, ready ${spokenWhen}. Tell the caller it's set.`);
    if (outcome === 'transfer') return say(`${PENDING.replace('{support}', spacedDigits(config.SUPPORT_SMS_NUMBER))} (${c.name}, ${spokenWhen})`);
    return FAILED;
  },
});

export const savePreferredPharmacy = llm.tool({
  name: 'save_preferred_pharmacy',
  description: 'Make a pharmacy the caller\'s usual pharmacy for future prescriptions.',
  parameters: z.object({
    candidate_ref: z.string().describe('The pharmacy exactly as you listed it.'),
  }),
  execute: async ({ candidate_ref }, { ctx }: { ctx: Ctx }) => {
    const state = ctx.userData;
    const c = gate(state, candidate_ref);
    if (typeof c === 'string') return c;
    if (config.DRY_RUN) return `(dry run) Saved ${c.name} as their usual pharmacy.`;
    try {
      await withTimeout(actions.setPreferred(state.patientToken, state.patientId, c.pharmacy_id, state.orderId), TIMEOUT_MS);
    } catch {
      return 'Couldn\'t save it right now. Tell them they can set it from the text link.';
    }
    return `Saved ${c.name} as their usual pharmacy. Acknowledge briefly.`;
  },
});

export const READ_TOOLS = [verifyIdentity, searchNearby, endCall];
export const WRITE_TOOLS = [choosePharmacy, setPickupTime, savePreferredPharmacy];
