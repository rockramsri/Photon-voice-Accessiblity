/**
 * Patient lookup via the Patient API, using the order-scoped JWT (x-photon-auth).
 * Patient-plane only — no clinical-api, no M2M token here.
 */
import { config } from '../config.js';
import { getLogger } from '../log.js';

const log = getLogger('photon.patient');

const QUERY = '{ me { name { first } phone dateOfBirth address { street1 city state postalCode } } }';

export interface PatientInfo {
  first: string | null;
  phone: string | null;
  address: Record<string, any> | null;
  dob: string | null;
}

const EMPTY: PatientInfo = { first: null, phone: null, address: null, dob: null };

export async function fetchPatient(jwt: string): Promise<PatientInfo> {
  let data: any;
  try {
    const resp = await fetch(config.PATIENT_API_URL, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'accept-encoding': 'gzip, deflate',
        'x-photon-auth': jwt,
      },
      body: JSON.stringify({ query: QUERY }),
    });
    data = await resp.json();
  } catch (exc) {
    log.error(`patient lookup failed: ${exc}`);
    return { ...EMPTY };
  }

  if (data?.errors) {
    log.error(`patient lookup GraphQL errors: ${JSON.stringify(data.errors)}`);
    return { ...EMPTY };
  }

  const me = data?.data?.me ?? {};
  const first = me?.name?.first ?? null;
  const phone = me?.phone ?? null;
  const address = me?.address ?? null;
  const dob = me?.dateOfBirth ?? null;
  log.info(`patient -> first=${first} phone=${Boolean(phone)} address=${Boolean(address)} dob=${Boolean(dob)}`);
  return { first, phone, address, dob };
}
