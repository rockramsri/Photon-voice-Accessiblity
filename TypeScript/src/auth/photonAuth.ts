/**
 * Photon machine-to-machine (M2M) token — mint once, cache, rotate on 401.
 * SECURITY: org-wide token. NEVER put it in room/agent metadata. Only the
 * per-order patient JWT (patientToken.ts) goes to the agent.
 */
import { config } from '../config.js';
import { getLogger } from '../log.js';

const log = getLogger('auth.photon');

let token = '';
let expiresAt = 0;
let inflight: Promise<string> | null = null;

export async function getM2mToken(forceRefresh = false): Promise<string> {
  const now = Date.now() / 1000;
  if (!forceRefresh && token && now < expiresAt - 60) return token;
  // Collapse concurrent refreshes (stand-in for the asyncio lock).
  if (!inflight) {
    inflight = fetchToken()
      .then(([t, exp]) => {
        token = t;
        expiresAt = exp;
        return token;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

async function fetchToken(): Promise<[string, number]> {
  const payload = {
    client_id: config.PHOTON_CLIENT_ID,
    client_secret: config.PHOTON_CLIENT_SECRET,
    audience: config.PHOTON_AUDIENCE,
    grant_type: 'client_credentials',
  };
  const resp = await fetch(config.PHOTON_AUTH_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'accept-encoding': 'gzip, deflate' },
    body: JSON.stringify(payload),
  });
  const data: any = await resp.json();
  const accessToken = data?.access_token;
  if (!accessToken) throw new Error(`M2M token request failed: ${JSON.stringify(data)}`);
  const expiresIn = Number(data?.expires_in ?? 3600);
  log.info(`minted M2M token (valid ~${Math.round(expiresIn / 60)} min)`);
  return [accessToken, Date.now() / 1000 + expiresIn];
}
