/**
 * Prefetch — gather all patient-facing order context before we dial.
 * Fires the 5 patient-API queries in PARALLEL. We dial only after this returns,
 * so the budget is generous; the caps are just a safety net against a hung call.
 * Returns { order, offers, pharmacies, faqs, nearby, completeness, partial }.
 */
import { config } from '../config.js';
import { getLogger } from '../log.js';
import * as client from './client.js';
import type { Json } from './client.js';
import type { Point } from './geocode.js';
import * as queries from './queries.js';

const log = getLogger('photon.prefetch');

// Generous — prefetch runs BEFORE we dial. Safety caps against a hung connection.
export const TOTAL_BUDGET = 300_000;
export const PER_CALL = 60_000;

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timeout after ${ms}ms`)), ms);
    p.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      },
    );
  });
}

async function safe(p: Promise<client.GraphQLResponse>, timeout = PER_CALL): Promise<Json | null> {
  let data: client.GraphQLResponse;
  try {
    data = await withTimeout(p, timeout);
  } catch (exc) {
    log.warn(`prefetch call failed/timed out: ${exc}`);
    return null;
  }
  if (data.errors) log.warn(`prefetch call returned GraphQL errors: ${JSON.stringify(data.errors)}`);
  return data.data ?? {};
}

const FIELD: Record<string, string> = {
  order: 'order',
  offers: 'offerBundles',
  pharmacies: 'pharmacies',
  faqs: 'faqs',
  nearby: 'pharmaciesByLocation',
};

export async function prefetch(jwt: string, orderId: string, point: Point | null): Promise<Json> {
  const names = ['order', 'offers', 'pharmacies', 'faqs', 'nearby'];
  const coros: Promise<Json | null>[] = [
    safe(client.patientApi(queries.ORDER, { id: orderId }, jwt)),
    safe(client.patientApi(queries.OFFER_BUNDLES, { orderId }, jwt)),
    safe(client.patientApi(queries.PHARMACIES, {}, jwt)),
    safe(client.patientApi(queries.FAQS, { orderId }, jwt)),
    nearby(jwt, point),
  ];

  let results: (Json | null)[];
  try {
    results = await withTimeout(Promise.all(coros), TOTAL_BUDGET);
  } catch {
    log.warn(`prefetch exceeded ${TOTAL_BUDGET / 1000}s total budget`);
    results = names.map(() => null);
  }

  const ctx: Json = { partial: false };
  const completeness: Record<string, boolean> = {};
  names.forEach((name, i) => {
    const value = results[i] === null ? null : results[i]![FIELD[name]] ?? null;
    ctx[name] = value;
    completeness[name] = value !== null && value !== undefined;
  });
  ctx.completeness = completeness;
  log.info(`prefetch order=${orderId} completeness=${JSON.stringify(completeness)}`);
  const missing = Object.entries(completeness).filter(([, ok]) => !ok).map(([n]) => n);
  if (missing.length) log.warn(`prefetch order=${orderId} INCOMPLETE — missing: ${missing.join(', ')}`);
  return ctx;
}

async function nearby(jwt: string, point: Point | null): Promise<Json | null> {
  if (!point) {
    log.info('no geocoded point — skipping nearby query');
    return null;
  }
  const [lat, lng] = point;
  const base = { location: { latitude: lat, longitude: lng, radius: 100 }, includePrice: true };
  const [near, allDay] = await Promise.all([
    safe(client.patientApi(queries.PHARMACIES_BY_LOCATION, { ...base, limit: 20 }, jwt)),
    safe(client.patientApi(queries.PHARMACIES_BY_LOCATION, { ...base, limit: 3, is24hr: true }, jwt)),
  ]);
  if (near === null && allDay === null) return null;
  const rows = new Map<string, Json>();
  for (const data of [near, allDay]) {
    for (const p of (data?.pharmaciesByLocation as Json[]) ?? []) {
      if (p?.id) rows.set(p.id, p);
    }
  }
  return { pharmaciesByLocation: [...rows.values()] };
}

export async function prefetchFallback(orderId: string): Promise<Json> {
  const data = await safe(client.providerApi(queries.PROVIDER_FALLBACK, { id: orderId }, config.PHOTON_TOKEN));
  const order = data?.order ?? null;
  log.info(`prefetch FALLBACK order=${orderId} order_present=${order !== null}`);
  return {
    partial: true,
    order,
    offers: null,
    pharmacies: null,
    faqs: null,
    nearby: null,
    completeness: { order: order !== null, offers: false, pharmacies: false, faqs: false, nearby: false },
  };
}
