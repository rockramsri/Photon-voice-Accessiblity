"""Geocode the patient's street address -> (lat, lng) for the nearby query.

Uses the free US Census geocoder (no key, US addresses). Falls back to the
GEO_LAT/GEO_LNG env point when the address is missing or the lookup fails, and
returns None if there's nothing usable (the nearby query is then skipped).
"""

from __future__ import annotations

import logging

import aiohttp

from shared import config

log = logging.getLogger("photon.geocode")

_CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


async def geocode_address(address: dict | None) -> tuple[float, float] | None:
    """Return (lat, lng) for a patient address dict, or the env/None fallback."""
    oneline = _oneline(address)
    point = await geocode_text(oneline) if oneline else None
    return point or _env_point()


async def geocode_text(oneline: str) -> tuple[float, float] | None:
    """Geocode a one-line US street address. None if no match (no env fallback)."""
    params = {"address": oneline, "benchmark": "Public_AR_Current", "format": "json"}
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(_CENSUS_URL, params=params,
                                timeout=aiohttp.ClientTimeout(total=4)) as resp:
                data = await resp.json()
        matches = (data.get("result") or {}).get("addressMatches") or []
        if matches:
            coords = matches[0]["coordinates"]  # x = lng, y = lat
            lat, lng = float(coords["y"]), float(coords["x"])
            log.info("geocoded address -> %.5f, %.5f", lat, lng)
            return lat, lng
        log.warning("geocoder found no match")
    except Exception as exc:  # noqa: BLE001 — geocoding is best-effort
        log.warning("geocode failed: %s", exc)
    return None


def _oneline(address: dict | None) -> str:
    if not address:
        return ""
    parts = [address.get("street1"), address.get("city"),
             address.get("state"), address.get("postalCode")]
    return ", ".join(p for p in parts if p)


def _env_point() -> tuple[float, float] | None:
    if config.GEO_LAT and config.GEO_LNG:
        try:
            return float(config.GEO_LAT), float(config.GEO_LNG)
        except ValueError:
            log.warning("GEO_LAT/GEO_LNG are not numbers — skipping nearby query")
    return None
