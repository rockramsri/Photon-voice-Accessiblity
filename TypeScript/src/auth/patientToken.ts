/**
 * Per-order patient JWT — the token patient-api accepts via `x-photon-auth`.
 *   1. createShortlink(type: MARKETPLACE) on the Clinical API (M2M token) -> sl.neutron.health URL.
 *   2. GET that URL WITHOUT following redirects; read ?token= from the Location header.
 * The JWT is scoped to ONE order, so it is safe to hand to the voice agent.
 */
import { config } from '../config.js';
import { getLogger } from '../log.js';
import { getM2mToken } from './photonAuth.js';

const log = getLogger('auth.patient_token');

const CREATE_SHORTLINK = `
mutation ($input: ShortlinkInput!) {
  createShortlink(input: $input) { url shortcode }
}
`;

export async function mintPatientJwt(orderId: string, patientId: string): Promise<string | null> {
  const url = await createShortlink(orderId, patientId);
  if (!url) return null;
  return tokenFromShortlink(url);
}

async function createShortlink(orderId: string, patientId: string, retry = true): Promise<string | null> {
  let data: any;
  try {
    const token = await getM2mToken(!retry); // retry path forces a fresh M2M token
    const resp = await fetch(config.CLINICAL_API_URL, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'accept-encoding': 'gzip, deflate',
        'x-photon-auth-token': token,
        'x-photon-auth-token-type': 'auth0',
      },
      body: JSON.stringify({
        query: CREATE_SHORTLINK,
        variables: {
          input: { patientId, orderId, type: 'MARKETPLACE', channelName: 'voice-agent' },
        },
      }),
    });
    data = await resp.json();
  } catch (exc) {
    log.error(`createShortlink request failed for order ${orderId}: ${exc}`);
    return null;
  }

  if (retry && isAuthError(data)) {
    log.warn('createShortlink auth error — refreshing M2M token and retrying');
    return createShortlink(orderId, patientId, false);
  }

  const link = data?.data?.createShortlink;
  if (!link) {
    log.error(`createShortlink failed for order ${orderId}: ${JSON.stringify(data?.errors)}`);
    return null;
  }
  log.info(`created shortlink for order ${orderId}`);
  return link.url as string;
}

async function tokenFromShortlink(url: string): Promise<string | null> {
  let location = '';
  try {
    const resp = await fetch(url, {
      method: 'GET',
      redirect: 'manual', // do NOT follow — the JWT lives in the 302 Location
      headers: { 'accept-encoding': 'gzip, deflate' },
    });
    location = resp.headers.get('location') ?? '';
  } catch (exc) {
    log.error(`shortlink GET failed: ${exc}`);
    return null;
  }
  const token = new URL(location, 'https://sl.neutron.health').searchParams.get('token');
  if (!token) {
    log.error(`no token in shortlink redirect: ${location.slice(0, 120)}`);
    return null;
  }
  return token;
}

function isAuthError(data: any): boolean {
  for (const err of data?.errors ?? []) {
    const code = String(err?.extensions?.code ?? '');
    if (code.toUpperCase().includes('AUTH')) return true;
  }
  return false;
}
