/**
 * CloudEvents parsing + Photon webhook signature verification (LOG-ONLY).
 * Body may be a JSON array of events OR a single event object — normalise to a list.
 */
import crypto from 'node:crypto';
import { getLogger } from '../log.js';

const log = getLogger('photon.events');

export const SIGNATURE_HEADER = 'x-photon-signature';
export const TIMESTAMP_HEADER = 'x-photon-timestamp';

export function parseEvents(raw: Buffer | string): Array<Record<string, any>> {
  let payload: any;
  try {
    payload = JSON.parse((raw?.toString() || 'null') as string);
  } catch (exc) {
    log.error(`could not parse webhook body as JSON: ${exc}`);
    return [];
  }
  if (payload === null) return [];
  if (Array.isArray(payload)) return payload.filter((e) => e && typeof e === 'object');
  if (typeof payload === 'object') return [payload];
  log.warn(`unexpected webhook payload type: ${typeof payload}`);
  return [];
}

export function verifySignature(
  raw: Buffer,
  timestamp: string,
  signature: string,
  secret: string,
): { present: boolean; matched: string | null } {
  const result: { present: boolean; matched: string | null } = { present: Boolean(signature), matched: null };
  if (!signature) {
    log.warn(`webhook has no ${SIGNATURE_HEADER} header — cannot verify (log-only)`);
    return result;
  }
  if (!secret) {
    log.warn('SHARED_SECRET unset — skipping signature check (log-only)');
    return result;
  }
  const candidates: Record<string, Buffer | null> = {
    raw,
    'timestamp.raw': timestamp ? Buffer.concat([Buffer.from(`${timestamp}.`), raw]) : null,
  };
  for (const [name, message] of Object.entries(candidates)) {
    if (message === null) continue;
    const expected = crypto.createHmac('sha256', secret).update(message).digest('hex');
    if (expected.length === signature.length && crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature))) {
      result.matched = name;
      log.info(`webhook signature OK — matched variant '${name}' (log-only)`);
      return result;
    }
  }
  log.warn('webhook signature MISMATCH on all variants (log-only; not rejecting)');
  return result;
}
