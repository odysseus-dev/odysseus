"""No-key weather lookup backed by Open-Meteo."""

import asyncio
import json
import re
import urllib.parse
import urllib.request


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Odysseus/1.0"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def weather_location_from_query(query: str) -> str | None:
    """Extract a place only from straightforward weather lookup phrasing."""
    text = re.sub(r"\s+", " ", query).strip(" ?.! ")
    patterns = (
        r"^(?:what(?:'s| is) the )?(?:current |today(?:'s)? |tomorrow(?:'s)? )?"
        r"(?:weather|forecast)(?: like)? (?:in|for|at) (?P<place>.+)$",
        r"^(?:weather|forecast) (?P<place>.+)$",
        r"^(?P<place>.+?) (?:weather|forecast)\b.*$",
    )
    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            place = re.sub(r"\b(?:today|tomorrow|now|current)\b.*$", "", match.group("place"), flags=re.IGNORECASE).strip(" ,")
            if 1 <= len(place) <= 100:
                return place
    return None


class WeatherTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            args = json.loads(content) if content.strip().startswith("{") else {"location": content}
            if not isinstance(args, dict):
                return {"error": "get_weather expects a location string or JSON object", "exit_code": 1}
            location = str(args.get("location") or "").strip()
            if not location or len(location) > 160:
                return {"error": "get_weather requires a location (up to 160 characters)", "exit_code": 1}

            geo_url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({
                "name": location, "count": 1, "language": "en", "format": "json",
            })
            geo = await asyncio.to_thread(_get_json, geo_url)
            places = geo.get("results") or []
            if not places:
                return {"error": f"No location found for {location!r}", "exit_code": 1}
            place = places[0]
            forecast_url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "forecast_days": 3,
                "timezone": place.get("timezone") or "auto",
            })
            forecast = await asyncio.to_thread(_get_json, forecast_url)
            current = forecast.get("current") or {}
            daily = forecast.get("daily") or {}
            if not current.get("time") or not daily.get("time"):
                return {"error": "Weather provider returned incomplete forecast data", "exit_code": 1}
            place_parts = list(dict.fromkeys(filter(None, [place.get("name"), place.get("admin1"), place.get("country")])))
            data = {
                "location": ", ".join(place_parts),
                "timezone": forecast.get("timezone"),
                "current": current,
                "current_units": forecast.get("current_units") or {},
                "daily": daily,
                "daily_units": forecast.get("daily_units") or {},
                "source": forecast_url,
                "provider": "Open-Meteo",
            }
            return {"output": json.dumps(data, ensure_ascii=False), "exit_code": 0, "evidence_status": "available"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"error": f"Weather lookup failed: {exc}", "exit_code": 1}
