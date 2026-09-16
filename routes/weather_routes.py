# routes/weather_routes.py
# Same-origin weather proxy for the homescreen dashboard widget. Calls
# Open-Meteo (free, no API key/signup, generous rate limits) from the
# backend rather than the browser so the widget needs no CSP connect-src
# change and no key management. Results are cached in-process per
# location string for a few minutes since the dashboard re-fetches every
# time the welcome screen is shown.
import logging
import time

import httpx
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_CACHE_TTL_SECONDS = 15 * 60
_cache: dict[str, tuple[float, dict]] = {}

# WMO weather codes (used by Open-Meteo) collapsed into a small set of
# human labels — good enough for a one-line widget summary.
_CONDITION_LABELS = {
    0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


def _condition_label(code) -> str:
    try:
        return _CONDITION_LABELS.get(int(code), "—")
    except (TypeError, ValueError):
        return "—"


def setup_weather_routes() -> APIRouter:
    router = APIRouter(prefix="/api/weather", tags=["weather"])

    @router.get("")
    async def get_weather(location: str = Query(..., min_length=1, max_length=120)):
        key = location.strip().lower()
        if not key:
            return {"ok": False, "error": "empty_location"}

        cached = _cache.get(key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                geo_r = await client.get(_GEOCODE_URL, params={
                    "name": location.strip(), "count": 1,
                })
                geo_r.raise_for_status()
                geo_results = (geo_r.json() or {}).get("results") or []
                if not geo_results:
                    return {"ok": False, "error": "location_not_found"}
                place = geo_results[0]
                lat, lon = place.get("latitude"), place.get("longitude")

                fc_r = await client.get(_FORECAST_URL, params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto",
                    "temperature_unit": "celsius",
                })
                fc_r.raise_for_status()
                fc = fc_r.json() or {}
        except Exception as e:
            logger.warning("weather fetch for %r failed: %s", location, e)
            return {"ok": False, "error": "unavailable"}

        current = fc.get("current") or {}
        daily = fc.get("daily") or {}
        label_parts = [p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p]

        result = {
            "ok": True,
            "location_label": ", ".join(label_parts[:2]) if label_parts else location,
            "temp": current.get("temperature_2m"),
            "condition_code": current.get("weather_code"),
            "condition_label": _condition_label(current.get("weather_code")),
            "high": (daily.get("temperature_2m_max") or [None])[0],
            "low": (daily.get("temperature_2m_min") or [None])[0],
        }
        _cache[key] = (time.time(), result)
        return result

    return router
