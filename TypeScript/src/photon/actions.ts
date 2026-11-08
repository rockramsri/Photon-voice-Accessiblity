/**
 * In-call Photon calls used by the agent's tools (patient-api, order JWT).
 * Logs carry operation + orderId + pharmacyId only (no PHI, no token).
 */
import { getLogger } from '../log.js';
import * as client from './client.js';
import type { Json } from './client.js';
import * as queries from './queries.js';

const log = getLogger('photon.actions');

export class PhotonError extends Error {}

export interface OrderStatus {
  id: string;
  state: string | null;
  isReroutable: boolean;
  readyByDay: string | null;
  readyByTime: string | null;
  pharmacy: { id: string; name: string | null } | null;
  fulfillment: { type: string | null; state: string | null } | null;
}

async function call(
  op: string,
  query: string,
  variables: Json,
  jwt: string,
  orderId = '',
  pharmacyId = '',
): Promise<Json> {
  log.info(`photon ${op} order=${orderId || '-'} pharmacy=${pharmacyId || '-'}`);
  const resp = await client.patientApi(query, variables, jwt);
  if (resp.errors) {
    log.warn(`photon ${op} failed: ${JSON.stringify(resp.errors.map((e) => e.message))}`);
    throw new PhotonError(op);
  }
  return resp.data ?? {};
}

export async function searchPharmacies(
  jwt: string,
  lat: number,
  lng: number,
  opts: { name?: string | null; is24hr?: boolean; limit?: number } = {},
): Promise<Json[]> {
  const variables = {
    location: { latitude: lat, longitude: lng, radius: 100 },
    limit: opts.limit ?? 50,
    includePrice: true,
    name: opts.name || null,
    is24hr: opts.is24hr || null,
  };
  const data = await call('pharmaciesByLocation', queries.PHARMACIES_BY_LOCATION, variables, jwt);
  return (data.pharmaciesByLocation as Json[]) ?? [];
}

export async function orderStatus(jwt: string, orderId: string): Promise<OrderStatus> {
  const data = await call('getOrderStatus', queries.ORDER_STATUS, { id: orderId }, jwt, orderId);
  if (!data.order) throw new PhotonError('order missing');
  const o = data.order as Json;
  return {
    id: o.id,
    state: o.state ?? null,
    isReroutable: Boolean(o.isReroutable),
    readyByDay: o.readyByDay ?? null,
    readyByTime: o.readyByTime ?? null,
    pharmacy: o.pharmacy ? { id: o.pharmacy.id, name: o.pharmacy.name ?? null } : null,
    fulfillment: o.fulfillment ? { type: o.fulfillment.type ?? null, state: o.fulfillment.state ?? null } : null,
  };
}

async function mutate(
  op: string,
  query: string,
  field: string,
  variables: Json,
  jwt: string,
  orderId: string,
  pharmacyId: string,
): Promise<void> {
  const data = await call(op, query, variables, jwt, orderId, pharmacyId);
  if (data[field] !== true) throw new PhotonError(`${op} returned ${JSON.stringify(data[field])}`);
}

export function reroute(
  jwt: string,
  orderId: string,
  pharmacyId: string,
  priceQuoted: boolean,
): Promise<void> {
  return mutate(
    'rerouteOrder',
    queries.REROUTE_ORDER,
    'rerouteOrder',
    { orderId, pharmacyId, patientSelectedPrice: priceQuoted, reason: 'patient phone selection' },
    jwt,
    orderId,
    pharmacyId,
  );
}

export function setOrderPharmacy(
  jwt: string,
  orderId: string,
  pharmacyId: string,
  priceQuoted: boolean,
  when: Date | null = null,
): Promise<void> {
  const variables: Json = { orderId, pharmacyId, patientSelectedPrice: priceQuoted };
  if (when) {
    const weekday = when.toLocaleDateString('en-US', { weekday: 'long', timeZone: 'America/New_York' });
    variables.readyByDay = weekday;
    variables.readyByTime = when.toISOString();
  }
  return mutate('setOrderPharmacy', queries.SET_ORDER_PHARMACY, 'setOrderPharmacy', variables, jwt, orderId, pharmacyId);
}

export async function waitForPharmacy(
  jwt: string,
  orderId: string,
  pharmacyId: string,
  tries = 3,
): Promise<OrderStatus> {
  let status = await orderStatus(jwt, orderId);
  for (let i = 0; i < tries - 1; i += 1) {
    if (status.pharmacy && status.pharmacy.id === pharmacyId) break;
    await new Promise((r) => setTimeout(r, 1000));
    status = await orderStatus(jwt, orderId);
  }
  return status;
}

export function setPreferred(
  jwt: string,
  patientId: string,
  pharmacyId: string,
  orderId: string,
): Promise<void> {
  return mutate(
    'setPreferredPharmacy',
    queries.SET_PREFERRED_PHARMACY,
    'setPreferredPharmacy',
    { patientId, pharmacyId },
    jwt,
    orderId,
    pharmacyId,
  );
}
