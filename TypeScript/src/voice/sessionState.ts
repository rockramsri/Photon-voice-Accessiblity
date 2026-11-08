/**
 * Per-call state, stored as AgentSession userData.
 * Turns what the caller says ("the second one", "Avenue O", "that CVS") into a
 * pharmacy id the Photon mutations need. Ids never leave this object.
 */
import type { Json } from '../photon/client.js';

export const FUZZY_MIN = 85;
const FILLER = new Set(['the', 'a', 'an', 'pharmacy', 'drugs', 'drug', 'store', 'option', 'one', 'at', 'on']);
const ORDINALS: Record<string, number> = {
  first: 1, one: 1, '1': 1, second: 2, two: 2, '2': 2,
  third: 3, three: 3, '3': 3, fourth: 4, four: 4, '4': 4,
  fifth: 5, five: 5, '5': 5, last: -1,
};

export type Source = 'nearby' | 'offer' | 'mailorder' | 'current';

export interface Candidate {
  pharmacy_id: string;
  name: string;
  source: Source;
  price?: number | null;
  retail?: number | null;
  close_spoken?: string | null;
  distance_spoken?: string | null;
  street?: string | null;
  sponsored?: boolean;
  price_quoted?: boolean;
}

export class ResolveError extends Error {}

// --- fuzzy string similarity (rapidfuzz token_set_ratio equivalent) ---
function lcsLen(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  const dp = new Array(n + 1).fill(0);
  for (let i = 1; i <= m; i += 1) {
    let prev = 0;
    for (let j = 1; j <= n; j += 1) {
      const tmp = dp[j];
      dp[j] = a[i - 1] === b[j - 1] ? prev + 1 : Math.max(dp[j], dp[j - 1]);
      prev = tmp;
    }
  }
  return dp[n];
}

/** fuzz.ratio: normalised Indel similarity *100. */
function ratio(a: string, b: string): number {
  if (!a.length && !b.length) return 100;
  return (2 * lcsLen(a, b)) / (a.length + b.length) * 100;
}

function tokenSetRatio(s1: string, s2: string): number {
  const t1 = new Set(s1.split(' ').filter(Boolean));
  const t2 = new Set(s2.split(' ').filter(Boolean));
  const inter = [...t1].filter((t) => t2.has(t)).sort();
  const diff1 = [...t1].filter((t) => !t2.has(t)).sort();
  const diff2 = [...t2].filter((t) => !t1.has(t)).sort();
  const sect = inter.join(' ');
  const ab = `${sect} ${diff1.join(' ')}`.trim();
  const ba = `${sect} ${diff2.join(' ')}`.trim();
  return Math.max(ratio(sect, ab), ratio(sect, ba), ratio(ab, ba));
}

function norm(name: string | null | undefined): string {
  const words = ((name || '').toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((w) => !FILLER.has(w));
  return words.join(' ');
}

function ambiguous(cands: Candidate[]): string {
  const label = (c: Candidate): string => {
    const detail = c.street || c.distance_spoken;
    if (c.street) return `${c.name} on ${detail}`;
    return detail ? `${c.name}, ${detail}` : c.name;
  };
  const names = cands.slice(0, 3).map(label).join(' or ');
  return `More than one pharmacy matches. Ask whether they meant ${names}.`;
}

export class SessionState {
  orderId: string;
  patientId: string;
  patientToken: string;
  orgName: string;
  firstName: string | null = null;
  phone: string | null = null;
  dob: string | null = null;
  canOfferChange = false;
  fulfillmentState: string | null = null;
  fulfillmentType: string | null = null;
  currentPharmacyId: string | null = null;
  medications: string[] = [];
  medShort = 'prescription';
  point: [number, number] | null = null;
  searchResults: Json[] = [];
  searchSig: unknown = null;
  searchCursor = 0;
  candidates: Map<string, Candidate> = new Map();
  lastOptions: string[] = [];
  identityVerified = false;
  dobAttempts = 0;
  wrapUpOffered = false;
  endReason: string | null = null;
  /** Set by the entrypoint; end_call invokes it to shut the job down. */
  endCall: () => void = () => {};

  constructor(opts: { orderId: string; patientId: string; patientToken: string; orgName: string }) {
    this.orderId = opts.orderId;
    this.patientId = opts.patientId;
    this.patientToken = opts.patientToken;
    this.orgName = opts.orgName;
  }

  static fromSeed(seed: Json, patientToken: string, orgFallback: string): SessionState {
    const state = new SessionState({
      orderId: seed.orderId,
      patientId: seed.patientId,
      patientToken,
      orgName: seed.orgName || orgFallback,
    });
    state.firstName = seed.patientFirstName ?? null;
    state.phone = seed.patientPhone ?? null;
    state.dob = seed.dob ?? null;
    state.canOfferChange = Boolean(seed.canOfferChange);
    state.fulfillmentState = seed.fulfillmentState ?? null;
    state.fulfillmentType = seed.fulfillmentType ?? null;
    state.currentPharmacyId = seed.currentPharmacyId ?? null;
    state.medications = seed.medications ?? [];
    state.medShort = seed.medShort || 'prescription';
    state.point = seed.point ? [seed.point[0], seed.point[1]] : null;
    state.add((seed.candidates ?? []).map((c: Json) => ({ ...c } as Candidate)));
    state.lastOptions = [...(seed.lastOptions ?? [])];
    return state;
  }

  get current(): Candidate | undefined {
    return this.candidates.get(this.currentPharmacyId ?? '');
  }

  add(found: Candidate[], spoken: string[] | null = null): void {
    for (const c of found) {
      const old = this.candidates.get(c.pharmacy_id);
      if (old && old.source === 'current') c.source = 'current';
      this.candidates.set(c.pharmacy_id, c);
    }
    if (spoken !== null) this.lastOptions = spoken;
  }

  resolve(ref: string): Candidate {
    const refNorm = norm(ref);
    const all = [...this.candidates.values()];
    const exact = all.filter((c) => norm(c.name) === refNorm && refNorm);
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) throw new ResolveError(ambiguous(exact));

    const byOrdinal = this.byOrdinal(ref);
    if (byOrdinal) return byOrdinal;

    const scored = all
      .map((c) => ({ score: tokenSetRatio(refNorm, norm(c.name)), c }))
      .sort((a, b) => b.score - a.score);
    const top = scored.length ? scored[0].score : 0;
    const hits = scored.filter((s) => s.score >= FUZZY_MIN && s.score >= top - 3).map((s) => s.c);
    if (hits.length === 1) return hits[0];
    if (hits.length > 1) throw new ResolveError(ambiguous(hits));
    throw new ResolveError(
      `Couldn't find a pharmacy matching '${ref}'. Ask the caller to say the name again, ` +
        'or offer to search nearby.',
    );
  }

  private byOrdinal(ref: string): Candidate | undefined {
    const words = ref.toLowerCase().match(/[a-z0-9]+/g) ?? [];
    if (!this.lastOptions.length || words.length > 4) return undefined;
    for (const word of words) {
      const n = ORDINALS[word];
      if (n === undefined) continue;
      const idx = n === -1 ? this.lastOptions.length - 1 : n - 1;
      if (idx >= 0 && idx < this.lastOptions.length) return this.candidates.get(this.lastOptions[idx]);
    }
    return undefined;
  }
}
