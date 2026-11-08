/**
 * Central configuration — the single place that reads env vars.
 * Mirrors the Python shared/config.py. Paths resolve to the project root
 * (TypeScript/), where .env, prompts/, and data dirs live.
 */
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import dotenv from 'dotenv';

// Anchor to the folder holding package.json so dev (tsx: src/config.ts) and
// prod (node: dist/src/config.js) both resolve to the project root (prompts/, .env).
function findRoot(start: string): string {
  let dir = start;
  for (let i = 0; i < 8; i += 1) {
    if (fs.existsSync(path.join(dir, 'package.json'))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return start;
}

export const ROOT = findRoot(path.dirname(fileURLToPath(import.meta.url)));
dotenv.config({ path: path.join(ROOT, '.env') });

const env = (name: string, fallback = ''): string => process.env[name] ?? fallback;
const envBool = (name: string, fallback: boolean): boolean => {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(raw.trim().toLowerCase());
};
const envNum = (name: string, fallback: number): number => {
  const raw = process.env[name];
  const n = raw === undefined || raw.trim() === '' ? NaN : Number(raw);
  return Number.isFinite(n) ? n : fallback;
};

export const config = {
  // --- Photon / Neutron ---
  SHARED_SECRET: env('SHARED_SECRET'),
  PHOTON_TOKEN: env('PHOTON_TOKEN'),
  PHOTON_CLIENT_ID: env('PHOTON_CLIENT_ID') || env('CLIENT_ID'),
  PHOTON_CLIENT_SECRET: env('PHOTON_CLIENT_SECRET') || env('CLIENT_SECRET'),
  PHOTON_AUTH_URL: env('PHOTON_AUTH_URL', 'https://auth.neutron.health/oauth/token'),
  PHOTON_AUDIENCE: env('PHOTON_AUDIENCE', 'https://api.neutron.health'),
  CLINICAL_API_URL: env('CLINICAL_API_URL', 'https://clinical-api.neutron.health/graphql'),
  PATIENT_API_URL: env('PATIENT_API_URL', 'https://patient-api.neutron.health/graphql'),
  PROVIDER_API_URL: env('PROVIDER_API_URL', 'https://api.neutron.health/graphql'),

  // --- Geocoding fallback point ---
  GEO_LAT: env('GEO_LAT'),
  GEO_LNG: env('GEO_LNG'),

  // --- LiveKit transport ---
  LIVEKIT_URL: env('LIVEKIT_URL'),
  LIVEKIT_API_KEY: env('LIVEKIT_API_KEY'),
  LIVEKIT_API_SECRET: env('LIVEKIT_API_SECRET'),

  // --- SIP / Twilio ---
  SIP_OUTBOUND_TRUNK_ID: env('SIP_OUTBOUND_TRUNK_ID') || env('LIVEKIT_SIP_OUTBOUND_TRUNK_ID'),
  TWILIO_CALLER_NUMBER:
    env('TWILIO_CALLER_NUMBER') || env('TWILIO_PHONE_NUMBER') || env('TWILIO_VOICE_NUMBER'),

  // --- Agent worker ---
  AGENT_NAME: env('AGENT_NAME', 'photon-greeter'),
  CALLBACK_DELAY_S: envNum('CALLBACK_DELAY_S', 300),
  DISPATCH_CALLBACK_URL: env('DISPATCH_CALLBACK_URL'),
  AGENT_DISPLAY_NAME: env('AGENT_DISPLAY_NAME', 'Hannah'),
  ORG_NAME: env('ORG_NAME', 'your pharmacy'),
  SUPPORT_SMS_NUMBER: env('SUPPORT_SMS_NUMBER', '513-866-3212'),
  DRY_RUN: envBool('DRY_RUN', false),
  CONSOLE_ORDER_ID: env('CONSOLE_ORDER_ID'),

  // --- Engine ---
  LLM_MODEL: env('LLM_MODEL', 'gpt-4.1-mini'),
  EXPRESSIVE: envBool('EXPRESSIVE', true),
  TTS_MODEL: env('TTS_MODEL', 'fishaudio/s2.1-pro'),
  TTS_VOICE: env('TTS_VOICE', '9a9cf47702da476aa4629e2506d4a857'),
  TTS_SPEED: envNum('TTS_SPEED', 1.0),
  EOT_MIN_DELAY: envNum('EOT_MIN_DELAY', 0.4),
  EOT_MAX_DELAY: envNum('EOT_MAX_DELAY', 1.6),
  EOT_THRESHOLD: envNum('EOT_THRESHOLD', 0.25),
  USER_AWAY_S: envNum('USER_AWAY_S', 6),
  OPENAI_API_KEY: env('OPENAI_API_KEY'),
  DEEPGRAM_API_KEY: env('DEEPGRAM_API_KEY'),

  // --- Test redirect ---
  TEST_PHONE_OVERRIDE: env('TEST_PHONE_OVERRIDE'),

  // --- Webhook server ---
  PORT: envNum('PORT', 3000),

  // --- Local files (gitignored; may hold PHI) ---
  ROOT,
  STATE_FILE: path.join(ROOT, 'state', 'processed_orders.json'),
  CONTEXT_DIR: path.join(ROOT, 'context'),
  CALLS_LOG: path.join(ROOT, 'calls.jsonl'),
  PERSONA_FILE: path.join(ROOT, 'prompts', 'voice_persona.txt'),
};
