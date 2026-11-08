/**
 * Photon GraphQL transport — the one place that POSTs to Neutron APIs.
 *   - patientApi(query, vars, jwt)    -> header x-photon-auth: <patient JWT>
 *   - clinicalApi(query, vars, token) -> header x-photon-auth-token: <M2M token>
 *   - providerApi(query, vars, token) -> header Authorization: Bearer <token>
 */
import { config } from '../config.js';

export type Json = Record<string, any>;
export interface GraphQLResponse {
  data?: Json | null;
  errors?: Array<{ message?: string; extensions?: Json }> | null;
}

const BASE_HEADERS: Record<string, string> = {
  'content-type': 'application/json',
  'accept-encoding': 'gzip, deflate',
};

async function post(
  url: string,
  headers: Record<string, string>,
  query: string,
  variables: Json | undefined,
  timeoutMs: number,
): Promise<GraphQLResponse> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { ...BASE_HEADERS, ...headers },
      body: JSON.stringify({ query, variables: variables ?? {} }),
      signal: ctrl.signal,
    });
    return (await resp.json()) as GraphQLResponse;
  } finally {
    clearTimeout(timer);
  }
}

export function patientApi(query: string, variables: Json | undefined, jwt: string, timeout = 4000) {
  return post(config.PATIENT_API_URL, { 'x-photon-auth': jwt }, query, variables, timeout);
}

export function clinicalApi(query: string, variables: Json | undefined, token: string, timeout = 6000) {
  return post(
    config.CLINICAL_API_URL,
    { 'x-photon-auth-token': token, 'x-photon-auth-token-type': 'auth0' },
    query,
    variables,
    timeout,
  );
}

export function providerApi(query: string, variables: Json | undefined, token: string, timeout = 4000) {
  return post(config.PROVIDER_API_URL, { authorization: `Bearer ${token}` }, query, variables, timeout);
}
