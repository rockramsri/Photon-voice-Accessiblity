/**
 * Turn a raw prefetch dict into a voice-ready prompt block.
 * Pure functions: no network, no env. Mirrors Python voice/shape_context.py.
 */
import type { Json } from '../photon/client.js';

const ONES = [
  'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
  'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
  'seventeen', 'eighteen', 'nineteen',
];
const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];

function underHundred(n: number): string {
  if (n < 20) return ONES[n];
  const ten = Math.floor(n / 10);
  const one = n % 10;
  return one === 0 ? TENS[ten] : `${TENS[ten]}-${ONES[one]}`;
}

function underThousand(n: number): string {
  if (n < 100) return underHundred(n);
  const hundreds = Math.floor(n / 100);
  const rest = n % 100;
  const head = `${ONES[hundreds]} hundred`;
  return rest === 0 ? head : `${head} ${underHundred(rest)}`;
}

export function numberToWords(n: number): string {
  if (n < 1000) return underThousand(n);
  const thousands = Math.floor(n / 1000);
  const rest = n % 1000;
  const head = `${underThousand(thousands)} thousand`;
  return rest === 0 ? head : `${head} ${underThousand(rest)}`;
}

export function wordsForNumbers(text: string): string {
  return text.replace(/(?<![\d.])\d+(?![\d.])/g, (m) => numberToWords(parseInt(m, 10)));
}

export function moneyToWords(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return 'an unknown price';
  let dollars = Math.trunc(amount);
  let cents = Math.round((amount - dollars) * 100);
  if (cents === 100) {
    dollars += 1;
    cents = 0;
  }
  let spoken = underThousand(dollars) + (dollars === 1 ? ' dollar' : ' dollars');
  if (cents) spoken += ` ${underHundred(cents)}`;
  return spoken;
}

export function ordinalWord(n: number): string {
  return underHundred(n);
}

export function spacedDigits(digits: string): string {
  let d = digits.replace(/\D/g, '');
  if (d.length === 11 && d.startsWith('1')) d = d.slice(1);
  const groups = d.length === 10 ? [d.slice(0, 3), d.slice(3, 6), d.slice(6)] : [d];
  return groups.map((g) => [...g].map((c) => ONES[Number(c)]).join(' ')).join(', ');
}

const MD_LINK = /\[([^\]]+)\]\([^)]*\)/g;
const URL_RE = /https?:\/\/([^\s/)]+)\S*/g;
const PHONE_RE = /(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)/g;
const MONEY_RE = /\$(\d+(?:\.\d{1,2})?)/g;
const MARKUP_RE = /\*+|`+|^#+\s*|^\s*(?:[-•]|\d+\.)\s+/gm;

export function sanitizeForVoice(text: string | null | undefined): string {
  let out = (text || '').replace(MD_LINK, '$1');
  out = out.replace(URL_RE, (_m, host: string) => host.replace(/\./g, ' dot '));
  out = out.replace(PHONE_RE, (m) => spacedDigits(m));
  out = out.replace(MONEY_RE, (_m, amt: string) => moneyToWords(parseFloat(amt)));
  out = out.replace(MARKUP_RE, '');
  return out.replace(/\s+/g, ' ').trim();
}

export function spokenStrength(name: string): string {
  let out = (name || '').replace(/(\d+(?:\.\d+)?)\s*MG\s*\/\s*(\d+(?:\.\d+)?)\s*ML\b/gi, '$1 milligrams per $2 milliliters');
  out = out.replace(/(\d+(?:\.\d+)?)\s*MCG\b/gi, '$1 micrograms');
  return out.replace(/(\d+(?:\.\d+)?)\s*MG\b/gi, '$1 milligrams');
}

const FORMS = ['tablet', 'capsule', 'suspension', 'solution', 'syrup', 'cream', 'ointment', 'injection', 'inhaler', 'patch', 'drops'];
const STRENGTH = /\d+(?:\.\d+)? (?:milligrams|micrograms)(?: per \d+(?:\.\d+)? milliliters)?/;

export function spokenMed(name: string | null | undefined): [string, string] {
  const text = spokenStrength(name || '').toLowerCase();
  const words = text.match(/[a-z][a-z-]*/g);
  if (!words || words.length === 0) return ['your prescription', 'prescription'];
  const generic = words[0];
  const full = generic + (/extended release|\ber\b|\bxr\b/.test(text) ? ' extended-release' : '');
  const parts = [full];
  const strength = text.match(STRENGTH);
  if (strength) parts.push(strength[0]);
  const form = FORMS.find((f) => text.includes(f));
  if (form) parts.push(form === 'tablet' || form === 'capsule' ? `${form}s` : form);
  return [wordsForNumbers(parts.join(', ')), generic];
}

export function dedupeMeds(fills: Json[] | null | undefined): Array<[string, string]> {
  const seen = new Set<string>();
  const meds: Array<[string, string]> = [];
  (fills ?? []).forEach((fill, idx) => {
    const rx = (fill ?? {}).prescription ?? {};
    const rxId = rx.id ?? `__idx_${idx}`;
    if (seen.has(rxId)) return;
    seen.add(rxId);
    meds.push(spokenMed(((fill ?? {}).treatment ?? {}).name));
  });
  return meds;
}

export function humanizeRouting(order: Json | null | undefined): string {
  const history = (order?.metadata?.routingHistory ?? []) as Json[];
  if (!history.length) return 'no pharmacy chosen yet';
  const last = history[history.length - 1];
  const name = String(last?.pharmacy?.name ?? '').trim();
  if (!name) return 'no pharmacy chosen yet';
  if (last.selector === 'AUTO' && last.reason === 'SYSTEM_ROUTED_TO_OPEN_ORDER_PHARMACY') {
    return `currently headed to ${name} because you have another prescription there`;
  }
  return `currently headed to ${name}`;
}

export function canOfferChange(order: Json | null | undefined): boolean {
  const ux = order?.organization?.settings?.patientUx ?? {};
  return Boolean(order?.isReroutable && ux.enablePatientRerouting);
}

function is24hr(place: Json): boolean {
  const type = place?.nextEvents?.close?.type;
  return type === '24hr' || type === 'open24hr';
}

/** Parse an event's UTC ISO datetime into a naive-NY Date (UTC fields = NY wall clock). */
function eventTime(event: Json | null | undefined): Date | null {
  const iso = event?.datetime;
  if (!iso) return null;
  const instant = new Date(iso);
  if (Number.isNaN(instant.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric', hour12: false,
  }).formatToParts(instant);
  const get = (t: string) => Number(parts.find((p) => p.type === t)!.value);
  let h = get('hour');
  if (h === 24) h = 0;
  return new Date(Date.UTC(get('year'), get('month') - 1, get('day'), h, get('minute'), get('second')));
}

export function spokenClock(when: Date): string {
  const hour = when.getUTCHours();
  const minute = when.getUTCMinutes();
  const hour12 = hour % 12 || 12;
  let clock = underHundred(hour12);
  if (minute) clock += (minute < 10 ? ' oh ' : ' ') + underHundred(minute);
  let part: string;
  if (hour < 12) part = 'in the morning';
  else if (hour < 17) part = 'in the afternoon';
  else part = 'tonight';
  return `${clock} ${part}`;
}

export function spokenHours(place: Json): string {
  if (is24hr(place)) return 'open 24 hours';
  const events = place.nextEvents ?? {};
  if (place.isOpen === false) {
    const when = eventTime(events.open);
    return when ? `closed now, opens ${spokenClock(when)}` : 'closed now';
  }
  const when = eventTime(events.close);
  return when ? `open until ${spokenClock(when)}` : 'hours unknown';
}

export function spokenDistance(miles: number | null | undefined): string {
  if (miles === null || miles === undefined) return 'nearby';
  if (miles < 0.3) return 'a few blocks away';
  return `about ${miles.toFixed(1)} miles`;
}

export function shapeOffers(bundles: Json[] | null | undefined): string[] {
  const lines: string[] = [];
  for (const bundle of (bundles ?? []).slice(0, 3)) {
    const pharmacy = String(bundle?.pharmacy?.name ?? 'a pharmacy').trim();
    const offer = (bundle.offers ?? [{}])[0] ?? {};
    const treatment = offer?.prescription?.treatment?.name ?? 'the medication';
    const price = offer.prescriptionPrice ?? {};
    const promise = offer?.deliveryEstimate?.deliveryPromise ?? 'delivery time unknown';
    let line = `${pharmacy}: ${treatment} about ${moneyToWords(price.amount)} (retail ${moneyToWords(price.retailAmount)}), ${promise}`;
    const tags = (bundle.attributeTags ?? []).map((t: Json) => t.kind);
    if (bundle.isPromoted || tags.includes('SPONSORED')) line += ' — this is a sponsored option';
    lines.push(line);
  }
  return lines;
}

const CHEAP_RADIUS_MI = 2.0;

export function rankNearby(places: Json[] | null | undefined): Json[] {
  const byDistance = [...(places ?? [])].sort(
    (a, b) => (a.distance ?? 1e9) - (b.distance ?? 1e9),
  );
  const openNow = byDistance.filter((p) => p.isOpen);
  const cheap = openNow.filter((p) => p.price !== null && p.price !== undefined && (p.distance ?? 0) <= CHEAP_RADIUS_MI);

  const closesAt = (p: Json): number => {
    if (is24hr(p)) return Infinity;
    const when = eventTime(p?.nextEvents?.close);
    return when ? when.getTime() : 0;
  };

  const minBy = (arr: Json[], key: (p: Json) => number): Json | null =>
    arr.reduce<Json | null>((best, p) => (best === null || key(p) < key(best) ? p : best), null);
  const maxBy = (arr: Json[], key: (p: Json) => number): Json | null =>
    arr.reduce<Json | null>((best, p) => (best === null || key(p) > key(best) ? p : best), null);

  const picks: Array<[Json | null, string]> = [
    [openNow[0] ?? null, 'closest'],
    [minBy(cheap, (p) => p.price), 'cheapest'],
    [maxBy(openNow, closesAt), 'latest-closing'],
  ];

  const picked: Json[] = [];
  for (const [place, label] of picks) {
    if (!place) continue;
    const same = picked.find((p) => p.id === place.id);
    if (same) same.labels.push(label);
    else picked.push({ ...place, labels: [label] });
  }
  for (const p of picked) {
    const labels: string[] = p.labels;
    delete p.labels;
    p.why = openNow.length === 1
      ? 'the only one open right now'
      : `the ${labels.join(' and ')} of the ones open now`;
  }
  const ids = new Set(picked.map((p) => p.id));
  return [...picked, ...byDistance.filter((p) => !ids.has(p.id))];
}

const STREET_WORDS: Record<string, string> = {
  ST: 'Street', AVE: 'Avenue', AV: 'Avenue', PKWY: 'Parkway', HWY: 'Highway',
  BLVD: 'Boulevard', RD: 'Road', DR: 'Drive', PL: 'Place', LN: 'Lane',
  W: 'West', E: 'East', N: 'North', S: 'South', STE: 'Suite',
};

export function spokenStreet(street1: string | null | undefined): string | null {
  if (!street1) return null;
  return street1
    .split(/\s+/)
    .map((w) => STREET_WORDS[w.toUpperCase().replace(/,/g, '')] ?? (w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()))
    .join(' ');
}

export function nearbyLine(place: Json): string {
  const name = String(place.name ?? 'a pharmacy').trim();
  const street = spokenStreet(place?.address?.street1);
  const why = place.why ? ` (${place.why})` : '';
  let line = `${name}${street ? ` on ${street}` : ''}${why}, ${spokenDistance(place.distance)}, `;
  line += spokenHours(place);
  const price = place.price;
  const retail = place.retailPrice;
  if (price !== null && price !== undefined) {
    line += `, around ${moneyToWords(price)} with a discount card`;
    if (retail && retail > price) line += ` (retail ${moneyToWords(retail)})`;
  }
  return line;
}

export function shapeNearby(places: Json[] | null | undefined): string[] {
  return rankNearby(places)
    .slice(0, 3)
    .map((p, i) => `Option ${ordinalWord(i + 1)}: ${nearbyLine(p)}`);
}

function mark(ok: boolean, empty = false): string {
  if (ok && empty) return '✗(empty)';
  return ok ? '✓' : '✗';
}

export function buildPromptBlock(ctx: Json): string {
  const order = ctx.order ?? {};
  const org = order?.organization?.name ?? 'the pharmacy';

  const meds = dedupeMeds(order.fills);
  const medsText = meds.length
    ? meds.map(([full, short]) => `${full} (after the first mention say 'your ${short}')`).join('; ')
    : "details unavailable, say 'your prescription'";

  const offers = shapeOffers(ctx.offers);
  const nearby = shapeNearby(ctx.nearby);
  const faqs = (ctx.faqs ?? []) as Json[];

  const lines = [
    `Organization: ${org}.`,
    `Medication, say the full name once, slowly: ${medsText}.`,
    `Routing: ${humanizeRouting(order)}.`,
    'Two ways to get it: pick it up at a nearby pharmacy, or have it mailed home by a mail-order pharmacy.',
  ];
  const pharmacy = order.pharmacy ?? {};
  if (pharmacy.name) {
    lines.push(`Current pharmacy: ${String(pharmacy.name).trim()} (${order?.fulfillment?.type ?? 'unknown'}).`);
  }

  if (canOfferChange(order)) {
    lines.push('You MAY discuss switching pharmacies if the caller asks.');
  } else {
    lines.push("Do NOT offer a pharmacy change. If asked, explain it can't be changed on this call and give the support path.");
  }
  if (order?.fulfillment?.state === 'SENT') {
    lines.push('Any change would be a transfer request, confirmed later by text.');
  }

  lines.push(`Marketplace offers: ${offers.length ? offers.join('; ') : 'none right now.'}`);
  lines.push(`Nearby pickup: ${nearby.length ? nearby.join('; ') : 'none available.'}`);

  const mail = ((ctx.pharmacies ?? []) as Json[]).filter((p) => p.name).map((p) => String(p.name).trim());
  if (mail.length) {
    lines.push(
      'Mail delivery pharmacies (mailed to their home; offer ONE at a time by name, ' +
        `never list several in one breath): ${mail.join(', ')}.`,
    );
  } else {
    lines.push('Mail delivery: none available right now.');
  }

  if (faqs.length) {
    lines.push('You may be asked:');
    for (const faq of faqs) {
      lines.push(`Q: ${sanitizeForVoice(faq.question)}`);
      lines.push(`A: ${sanitizeForVoice(faq.answer)}`);
    }
  }

  const comp = (ctx.completeness ?? {}) as Json;
  const offersEmpty = Array.isArray(ctx.offers) && ctx.offers.length === 0;
  lines.push(
    'context: ' +
      `order ${mark(comp.order ?? Boolean(order && Object.keys(order).length))} ` +
      `offers ${mark(comp.offers ?? ctx.offers !== null, offersEmpty)} ` +
      `nearby ${mark(comp.nearby ?? nearby.length > 0)} ` +
      `faqs ${mark(comp.faqs ?? faqs.length > 0)}`,
  );
  if (ctx.partial) {
    lines.push('NOTE: this context is partial — some lookups failed. Do not invent the missing parts.');
  }
  return lines.join('\n');
}

export function candidateFromPlace(place: Json, quoted: boolean): Json {
  return {
    pharmacy_id: place.id,
    name: String(place.name ?? '').trim(),
    source: 'nearby',
    price: place.price,
    retail: place.retailPrice,
    close_spoken: spokenHours(place),
    distance_spoken: spokenDistance(place.distance),
    street: spokenStreet(place?.address?.street1),
    price_quoted: quoted && place.price !== null && place.price !== undefined,
  };
}

export function buildSeed(
  ctx: Json,
  orderId: string,
  point: [number, number] | null,
  patient: Json,
  patientId: string,
): Json {
  const order = ctx.order ?? {};
  const fulfillment = order.fulfillment ?? {};
  const current = order.pharmacy ?? {};

  const candidates: Json[] = [];
  const nearby = rankNearby(ctx.nearby);
  nearby.forEach((place, i) => candidates.push(candidateFromPlace(place, i < 3)));
  for (const bundle of (ctx.offers ?? []) as Json[]) {
    const pharmacy = bundle.pharmacy ?? {};
    if (!pharmacy.id) continue;
    const price = (bundle.offers ?? [{}])[0]?.prescriptionPrice ?? {};
    const tags = (bundle.attributeTags ?? []).map((t: Json) => t.kind);
    candidates.push({
      pharmacy_id: pharmacy.id,
      name: String(pharmacy.name ?? '').trim(),
      source: 'offer',
      price: price.amount,
      retail: price.retailAmount,
      sponsored: Boolean(bundle.isPromoted || tags.includes('SPONSORED')),
      price_quoted: true,
    });
  }
  for (const p of (ctx.pharmacies ?? []) as Json[]) {
    if (p.id) candidates.push({ pharmacy_id: p.id, name: String(p.name ?? '').trim(), source: 'mailorder' });
  }
  if (current.id) {
    candidates.push({
      pharmacy_id: current.id,
      name: String(current.name ?? '').trim(),
      source: 'current',
      street: spokenStreet(current?.address?.street1),
    });
  }

  const meds = dedupeMeds(order.fills);
  return {
    orderId,
    patientId,
    patientFirstName: patient.first,
    patientPhone: patient.phone,
    dob: patient.dob,
    orgName: order?.organization?.name,
    canOfferChange: canOfferChange(order),
    fulfillmentState: fulfillment.state,
    fulfillmentType: fulfillment.type,
    currentPharmacyId: current.id,
    medications: meds.map(([full]) => full),
    medShort: meds.map(([, short]) => short).join(' and ') || 'prescription',
    point: point ? [...point] : null,
    lastOptions: nearby.slice(0, 3).filter((p) => p.id).map((p) => p.id),
    candidates,
  };
}
