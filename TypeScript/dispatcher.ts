/**
 * Dispatcher — turn a fresh order:created event into one outbound call.
 * dedupe -> mint order JWT -> fetch patient -> geocode -> prefetch -> shape -> dial.
 */
import { mintPatientJwt } from './src/auth/patientToken.js';
import * as outbound from './src/channels/outbound.js';
import { config } from './src/config.js';
import { saveContext } from './src/contextStore.js';
import { getLogger } from './src/log.js';
import type { Json } from './src/photon/client.js';
import * as dedupe from './src/photon/dedupe.js';
import { geocodeAddress } from './src/photon/geocode.js';
import { fetchPatient } from './src/photon/patient.js';
import { prefetch } from './src/photon/prefetch.js';
import { buildPromptBlock, buildSeed } from './src/voice/shapeContext.js';

const log = getLogger('photon.dispatcher');

export async function handleOrderCreated(data: Json, force = false): Promise<void> {
  const orderId = data?.id;
  if (!orderId) {
    log.warn('order:created with no data.id — ignoring');
    return;
  }

  // DEDUPE: webhooks redeliver. force=true (inbound callback) bypasses it.
  if (!force && dedupe.seen(orderId)) {
    log.info(`order ${orderId} already processed — skipping`);
    return;
  }

  const patientId = data?.patient?.id;
  if (!patientId) {
    log.error(`order ${orderId} has no patient id — cannot mint JWT, not dialing`);
    return;
  }
  const patientJwt = await mintPatientJwt(orderId, patientId);
  if (!patientJwt) {
    log.error(`patient JWT mint FAILED for order ${orderId} — not dialing`);
    return;
  }

  const patient = await fetchPatient(patientJwt);
  const to = config.TEST_PHONE_OVERRIDE || patient.phone;
  if (!to) {
    log.error(`order ${orderId} has no patient phone and no TEST_PHONE_OVERRIDE — not dialing`);
    return;
  }

  const point = await geocodeAddress(patient.address);
  const ctx = await prefetch(patientJwt, orderId, point);
  const promptBlock = buildPromptBlock(ctx);
  const seed = buildSeed(ctx, orderId, point, patient, patientId);
  saveContext(orderId, promptBlock, seed); // local fallback (console / same machine)

  const roomName = `photon-order-${orderId}`;
  // Context rides in the job metadata so the worker — a SEPARATE Railway service with
  // its own filesystem — gets it without a shared disk. patientJwt is single-order scoped.
  const metadata = JSON.stringify({ orderId, patientJwt, promptBlock, seed });
  log.info(`dialing order=${orderId} -> ${to}${config.TEST_PHONE_OVERRIDE ? ' (override)' : ''}`);
  const result = await outbound.placeCall(to, roomName, metadata);
  if (result.ok) {
    dedupe.mark(orderId);
    log.info(`order ${orderId} dialed, room=${result.room}`);
  } else {
    log.error(`order ${orderId} dial failed: ${result.error}`);
  }
}
