"""Central configuration — the single place that reads env vars.

Mirrors Rock-voice-scheduler-engine/shared/config.py: nothing else in this
project touches os.environ. Names follow the task spec; where the reference
repo used a different name we accept it as a fallback so the same .env works
for both projects.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# shared/ lives in Python/src/shared, so the project root (where .env, prompts/,
# and data dirs live) is three parents up.
_ROOT = Path(__file__).resolve().parents[2]

# This project's own .env carries everything the service needs.
load_dotenv(_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Photon / Neutron ---
SHARED_SECRET: str = os.getenv("SHARED_SECRET", "")
# Optional user token, ONLY for webhook admin (scripts/dev_tunnel.py). The M2M
# app token lacks webhook_config permission, so webhook create/delete needs
# either this user token or the M2M app granted webhook scopes in Photon.
PHOTON_TOKEN: str = os.getenv("PHOTON_TOKEN", "")

# --- Photon M2M auth (OAuth client_credentials) ---
# The backend mints a short-lived org token from these and rotates it on 401.
# Accept the experiment's CLIENT_ID/CLIENT_SECRET spelling as a fallback.
PHOTON_CLIENT_ID: str = os.getenv("PHOTON_CLIENT_ID", "") or os.getenv("CLIENT_ID", "")
PHOTON_CLIENT_SECRET: str = (
    os.getenv("PHOTON_CLIENT_SECRET", "") or os.getenv("CLIENT_SECRET", "")
)
PHOTON_AUTH_URL: str = os.getenv("PHOTON_AUTH_URL", "https://auth.neutron.health/oauth/token")
PHOTON_AUDIENCE: str = os.getenv("PHOTON_AUDIENCE", "https://api.neutron.health")

# --- Photon GraphQL endpoints ---
# Clinical (provider) API: used with the M2M token for lookups + createShortlink.
CLINICAL_API_URL: str = os.getenv("CLINICAL_API_URL", "https://clinical-api.neutron.health/graphql")
# Patient API: used by the agent with the per-order patient JWT (x-photon-auth).
PATIENT_API_URL: str = os.getenv("PATIENT_API_URL", "https://patient-api.neutron.health/graphql")
# Provider API (Bearer): used only for the prefetch fallback.
PROVIDER_API_URL: str = os.getenv("PROVIDER_API_URL", "https://api.neutron.health/graphql")

# --- Geocoding (nearby pharmacies query) ---
# We geocode the patient's street address at prefetch time; these are the
# fallback point when geocoding fails or no address is present.
# TODO: once address geocoding is trusted, drop these envs entirely.
GEO_LAT: str = os.getenv("GEO_LAT", "")
GEO_LNG: str = os.getenv("GEO_LNG", "")

# --- LiveKit transport ---
LIVEKIT_URL: str = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY: str = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET: str = os.getenv("LIVEKIT_API_SECRET", "")

# --- SIP / Twilio (reuse the Rock-scheduler trunk + number) ---
# Accept the reference repo's LIVEKIT_SIP_OUTBOUND_TRUNK_ID spelling as fallback.
SIP_OUTBOUND_TRUNK_ID: str = (
    os.getenv("SIP_OUTBOUND_TRUNK_ID", "")
    or os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "")
)
# Caller ID presented on the outbound call. Reference repo calls it
# TWILIO_PHONE_NUMBER; the spec calls it TWILIO_CALLER_NUMBER.
TWILIO_CALLER_NUMBER: str = (
    os.getenv("TWILIO_CALLER_NUMBER", "")
    or os.getenv("TWILIO_PHONE_NUMBER", "")
    or os.getenv("TWILIO_VOICE_NUMBER", "")
)

# --- Agent worker ---
# Named worker needs an explicit dispatch (see channels/outbound.place_call).
AGENT_NAME: str = os.getenv("AGENT_NAME", "photon-greeter")
# Inbound calls reuse the same agent (photon-greeter); entry.py branches when a
# job has no order. How long to wait before the inbound callback dials (>= 5 min).
CALLBACK_DELAY_S: int = int(os.getenv("CALLBACK_DELAY_S", "300"))
# The inbound agent POSTs here to enqueue the delayed callback on the long-lived server.
DISPATCH_CALLBACK_URL: str = os.getenv("DISPATCH_CALLBACK_URL", "")
# Name the agent introduces itself with on the call.
AGENT_DISPLAY_NAME: str = os.getenv("AGENT_DISPLAY_NAME", "Hannah")
# Org name spoken when the order carries none.
ORG_NAME: str = os.getenv("ORG_NAME", "your pharmacy")
# Spoken in handoffs ("text Photon support at ...").
SUPPORT_SMS_NUMBER: str = os.getenv("SUPPORT_SMS_NUMBER", "513-866-3212")
# Write tools skip the real Photon mutations (console testing).
DRY_RUN: bool = _env_bool("DRY_RUN", False)
# Console mode has no job metadata; this picks the context/<orderId>.json to load.
CONSOLE_ORDER_ID: str = os.getenv("CONSOLE_ORDER_ID", "")

# --- Engine (provider keys optional: empty routes via LiveKit Inference) ---
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4.1-mini")
# Expressive delivery tags (emotion, sighs, soft/whisper tone).
EXPRESSIVE: bool = _env_bool("EXPRESSIVE", True)
# LiveKit Inference TTS, no vendor key. Voice = Fish voice-library id (default: Adrian).
TTS_MODEL: str = os.getenv("TTS_MODEL", "fishaudio/s2.1-pro")
TTS_VOICE: str = os.getenv("TTS_VOICE", "9a9cf47702da476aa4629e2506d4a857")
# Speaking rate (1.0 felt rushed on calls).
TTS_SPEED: float = float(os.getenv("TTS_SPEED", "1.0"))
# Turn taking. Below EOT_THRESHOLD the caller is probably mid-sentence, so we
# wait up to EOT_MAX_DELAY; otherwise reply after ~EOT_MIN_DELAY. Keep max >= 1.2s
# or callers get cut off while reading a date of birth.
EOT_MIN_DELAY: float = float(os.getenv("EOT_MIN_DELAY", "0.4"))
EOT_MAX_DELAY: float = float(os.getenv("EOT_MAX_DELAY", "1.6"))
EOT_THRESHOLD: float = float(os.getenv("EOT_THRESHOLD", "0.25"))
# Seconds of mutual silence before a single "take your time" nudge.
USER_AWAY_S: float = float(os.getenv("USER_AWAY_S", "6"))
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")

# --- Confirmation SMS (optional; unset -> the SMS is only logged) ---
TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")

# --- Test redirect ---
# If set, calls and SMS go to this number instead of the patient's phone.
TEST_PHONE_OVERRIDE: str = os.getenv("TEST_PHONE_OVERRIDE", "")

# --- Webhook server ---
PORT: int = int(os.getenv("PORT", "3000"))

# --- Local files (all gitignored; may contain PHI) ---
ROOT: Path = _ROOT
STATE_FILE: Path = _ROOT / "state" / "processed_orders.json"
CONTEXT_DIR: Path = _ROOT / "context"
CALLS_LOG: Path = _ROOT / "calls.jsonl"
