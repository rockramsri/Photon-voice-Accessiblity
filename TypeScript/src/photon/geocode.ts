/**
 * Geocode the patient's street address -> [lat, lng] via the free US Census
 * geocoder. Falls back to the GEO_LAT/GEO_LNG env point, else null.
 */
import { config } from '../config.js';
import { getLogger } from '../log.js';

const log = getLogger('photon.geocode');
const CENSUS_URL = 'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress';

export type Point = [number, number];

export async function geocodeAddress(address: Record<string, any> | null): Promise<Point | null> {
  const oneline = toOneline(address);
  const point = oneline ? await geocodeText(oneline) : null;
  return point ?? envPoint();
}

export async function geocodeText(oneline: string): Promise<Point | null> {
  const params = new URLSearchParams({
    address: oneline,
    benchmark: 'Public_AR_Current',
    format: 'json',
  });
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4000);
    const resp = await fetch(`${CENSUS_URL}?${params}`, { signal: ctrl.signal });
    clearTimeout(timer);
    const data: any = await resp.json();
    const matches = data?.result?.addressMatches ?? [];
    if (matches.length) {
      const coords = matches[0].coordinates; // x = lng, y = lat
      const lat = Number(coords.y);
      const lng = Number(coords.x);
      log.info(`geocoded address -> ${lat.toFixed(5)}, ${lng.toFixed(5)}`);
      return [lat, lng];
    }
    log.warn('geocoder found no match');
  } catch (exc) {
    log.warn(`geocode failed: ${exc}`);
  }
  return null;
}

function toOneline(address: Record<string, any> | null): string {
  if (!address) return '';
  const parts = [address.street1, address.city, address.state, address.postalCode];
  return parts.filter(Boolean).join(', ');
}

function envPoint(): Point | null {
  if (config.GEO_LAT && config.GEO_LNG) {
    const lat = Number(config.GEO_LAT);
    const lng = Number(config.GEO_LNG);
    if (Number.isFinite(lat) && Number.isFinite(lng)) return [lat, lng];
    log.warn('GEO_LAT/GEO_LNG are not numbers — skipping nearby query');
  }
  return null;
}
