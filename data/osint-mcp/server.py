#!/usr/local/bin/python3
"""Read-only OSIRIS OSINT adapter for Odysseus.

Runs as a stdio MCP server inside the Odysseus container. It calls only the
keyless, read-only HTTP endpoints on https://osirisai.live. There is no key,
no account, no write operation, and no arbitrary URL input.
"""

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("osiris-osint")

BASE_URL = "https://osirisai.live"
USER_AGENT = "Odysseus-OSINT/1.0"
TIMEOUT_SECONDS = 25
MAX_RESPONSE_BYTES = 2_000_000
MAX_DASHBOARD_BYTES = 8_000_000
MAX_RESULT_CHARS = 14_000
MAX_DASHBOARD_ROWS = 50
MAX_DASHBOARD_LIMIT = 200
REGION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

LOOKUPS = {
    "dns": ("/api/osint/dns", "domain"),
    "whois": ("/api/osint/whois", "domain"),
    "certs": ("/api/osint/certs", "domain"),
    "ip": ("/api/osint/ip", "ip"),
    "shodan": ("/api/osint/shodan", "ip"),
    "portscan": ("/api/osint/portscan", "target"),
    "bgp": ("/api/osint/bgp", "query"),
    "mac": ("/api/osint/mac", "mac"),
    "phone": ("/api/osint/phone", "number"),
    "github": ("/api/osint/github", "user"),
    "leaks": ("/api/osint/leaks", "email"),
    "hudsonrock": ("/api/osint/hudsonrock", "query"),
    "cve": ("/api/osint/cve", "cve"),
    "sanctions": ("/api/osint/sanctions", "query"),
    "threats": ("/api/osint/threats", "query"),
}

FEEDS = {
    "stats": "/api/stats",
    "flights": "/api/flights",
    "satellites": "/api/satellites",
    "space-weather": "/api/space-weather",
    "earthquakes": "/api/earthquakes",
    "fires": "/api/fires",
    "weather": "/api/weather",
    "air-quality": "/api/air-quality",
    "radar": "/api/radar",
    "sentinel": "/api/sentinel",
    "conflicts": "/api/conflicts",
    "frontlines": "/api/frontlines",
    "gdelt": "/api/gdelt",
    "country-risk": "/api/country-risk",
    "region-dossier": "/api/region-dossier",
    "news": "/api/news",
    "live-news": "/api/live-news",
    "markets": "/api/markets",
    "crypto": "/api/crypto",
    "scm-suppliers": "/api/scm-suppliers",
    "cctv": "/api/cctv",
    "infrastructure": "/api/infrastructure",
    "maritime": "/api/maritime",
    "transit": "/api/transit",
    "geo": "/api/geo",
    "cyber-threats": "/api/cyber-threats",
    "cyber-attacks": "/api/cyber-attacks",
    "malware": "/api/malware",
    "oi-trending": "/api/oi/trending",
}

OPTIONAL_PARAMS = ("lat", "lng", "radius", "days", "region", "bbox", "schema", "limit", "type")

FLIGHT_FIELDS = (
    "callsign", "lat", "lng", "alt", "heading", "speed_knots",
    "model", "icao24", "registration", "category", "grounded",
)
SATELLITE_FIELDS = ("name", "noradId", "lat", "lng", "alt", "mission", "category")
CCTV_FIELDS = ("id", "name", "city", "country", "lat", "lng", "source", "stream_type")
CCTV_PREVIEW_FIELDS = CCTV_FIELDS + ("feed_url", "stream_url", "external_url")
MARITIME_FIELDS = (
    "id", "name", "country", "lat", "lng", "type", "volume",
    "rank", "congestion", "dwell_time",
)
NEWS_FIELDS = ("id", "name", "city", "country", "lat", "lng", "category", "language")
INCIDENT_FIELDS = ("id", "name", "lat", "lng", "type", "url")
EARTHQUAKE_FIELDS = ("id", "magnitude", "place", "lat", "lng", "depth", "time", "url", "alert")
FIRE_FIELDS = ("lat", "lng", "brightness", "confidence", "date", "time", "frp", "type")
CABLE_FIELDS = ("id", "name", "feature_id", "color", "length_km")

DASHBOARD_LAYERS = {
    "flights": {
        "path": "/api/flights", "kind": "bucket", "bucket": "commercial_flights",
        "fields": FLIGHT_FIELDS, "max_bytes": MAX_DASHBOARD_BYTES,
    },
    "private": {
        "path": "/api/flights", "kind": "bucket", "bucket": "private_flights",
        "fields": FLIGHT_FIELDS, "max_bytes": MAX_DASHBOARD_BYTES,
    },
    "jets": {
        "path": "/api/flights", "kind": "bucket", "bucket": "private_jets",
        "fields": FLIGHT_FIELDS, "max_bytes": MAX_DASHBOARD_BYTES,
    },
    "military": {
        "path": "/api/flights", "kind": "bucket", "bucket": "military_flights",
        "fields": FLIGHT_FIELDS, "max_bytes": MAX_DASHBOARD_BYTES,
    },
    "sdk_air": {
        "path": "/api/flights", "kind": "flights_summary",
        "fields": FLIGHT_FIELDS, "max_bytes": MAX_DASHBOARD_BYTES,
    },
    "maritime": {
        "path": "/api/maritime", "kind": "multi",
        "buckets": ("ports", "chokepoints", "ships"), "fields": MARITIME_FIELDS,
    },
    "satellites": {
        "path": "/api/satellites", "kind": "bucket", "bucket": "satellites",
        "fields": SATELLITE_FIELDS, "max_bytes": MAX_DASHBOARD_BYTES,
    },
    "cctv": {
        "path": "/api/cctv", "kind": "bucket", "bucket": "cameras",
        "fields": CCTV_FIELDS, "filter": "cctv",
    },
    "cctv_previews": {
        "path": "/api/cctv", "kind": "bucket", "bucket": "cameras",
        "fields": CCTV_PREVIEW_FIELDS, "filter": "cctv",
    },
    "live_news": {
        "path": "/api/live-news", "kind": "bucket", "bucket": "feeds",
        "fields": NEWS_FIELDS,
    },
    "earthquakes": {
        "path": "/api/earthquakes", "kind": "bucket", "bucket": "earthquakes",
        "fields": EARTHQUAKE_FIELDS,
    },
    "fires": {
        "path": "/api/fires", "kind": "bucket", "bucket": "fires",
        "fields": FIRE_FIELDS,
    },
    "global_incidents": {
        "path": "/api/gdelt", "kind": "bucket", "bucket": "events",
        "fields": INCIDENT_FIELDS,
    },
    "cables": {
        "path": "/data/submarine-cables.json", "kind": "cables",
        "bucket": "features", "fields": CABLE_FIELDS,
    },
    "sdk_sea": {
        "path": "/data/submarine-cables.json", "kind": "cables",
        "bucket": "features", "fields": CABLE_FIELDS,
    },
    "wind": {
        "path": "/api/wind", "kind": "wind", "filter": "wind",
    },
    "rain": {
        "path": "/api/rain", "kind": "rain",
    },
}

DERIVED_LAYERS = ("day_night", "live_clouds", "sdk_naval", "wind_controls", "rain_controls")


def _fetch(path, params, max_bytes=MAX_RESPONSE_BYTES, truncate=True):
    """Fetch one allowlisted endpoint and return a bounded response.

    Normal feeds return a truncated text result. Dashboard layers set
    ``truncate=False`` so they can project and cap rows before serialization.
    """
    clean = {k: str(v) for k, v in params.items() if v is not None and v != ""}
    query = urllib.parse.urlencode(clean)
    url = BASE_URL + path + ("?" + query if query else "")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(max_bytes + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", "replace")
        return {
            "ok": False,
            "error": "http_error",
            "status": exc.code,
            "url": url,
            "detail": detail,
        }
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "url": url, "detail": str(exc)[:300]}

    if len(raw) > max_bytes:
        return {"ok": False, "error": "response_too_large", "url": url}

    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return {"ok": False, "error": "invalid_json", "url": url, "detail": str(exc)[:200]}

    truncated = truncate and len(text) > MAX_RESULT_CHARS
    if truncated:
        text = text[:MAX_RESULT_CHARS] + "...[TRUNCATED]"
    out = {
        "ok": True,
        "status": status,
        "url": url,
        "characters": len(text),
        "truncated": truncated,
    }
    if truncate:
        out["result"] = text
    else:
        out["payload"] = payload
    return out


def _project(rows, fields, limit):
    out = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            out.append({key: row[key] for key in fields if key in row})
    return out


def _bounded(layer, source, url, payload, notes):
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    truncated = len(text) > MAX_RESULT_CHARS
    if truncated:
        text = text[:MAX_RESULT_CHARS] + "...[TRUNCATED]"
    return {
        "ok": True,
        "layer": layer,
        "source": source,
        "url": url,
        "characters": len(text),
        "truncated": truncated,
        "notes": notes,
        "result": text,
    }


def _cctv_params(region, lat, lng, radius):
    if region and (lat is not None or lng is not None):
        return {"error": "choose_region_or_radius"}
    if region:
        if not REGION_RE.match(region.strip()):
            return {"error": "invalid_region"}
        return {"params": {"region": region.strip()}}
    if lat is None or lng is None:
        return {"error": "cctv_filter_required"}
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        return {"error": "invalid_coordinates"}
    value = 25 if radius is None else radius
    if not 1 <= value <= 500:
        return {"error": "invalid_radius"}
    return {"params": {"lat": lat, "lng": lng, "radius": value}}


def _wind_sample(payload, lat, lng):
    try:
        ni = int(payload["ni"])
        nj = int(payload["nj"])
        dx = float(payload["dx"])
        dy = float(payload["dy"])
        la1 = float(payload["la1"])
        lo1 = float(payload["lo1"])
        us = payload["u"]
        vs = payload["v"]
    except (KeyError, TypeError, ValueError):
        return None
    if ni <= 0 or nj <= 0 or dx <= 0 or dy <= 0:
        return None
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        return None
    i = int(round((((lng - lo1) % 360) / dx))) % ni
    j = int(round((la1 - lat) / dy))
    if j < 0 or j >= nj:
        return None
    index = j * ni + i
    if index >= len(us) or index >= len(vs):
        return None
    u = float(us[index])
    v = float(vs[index])
    speed = math.hypot(u, v)
    toward = math.degrees(math.atan2(v, u)) % 360
    return {
        "lat": lat,
        "lng": lng,
        "grid_lat": round(la1 - j * dy, 3),
        "grid_lng": round((lo1 + i * dx) % 360, 3),
        "u_mps": round(u, 2),
        "v_mps": round(v, 2),
        "speed_mps": round(speed, 2),
        "speed_kmh": round(speed * 3.6, 2),
        "from_degrees": round((270 - toward) % 360, 1),
    }


def _compact_payload(key, spec, payload, limit, lat, lng):
    kind = spec["kind"]
    if kind == "bucket":
        bucket = spec["bucket"]
        rows = payload.get(bucket) or []
        shown = _project(rows, spec["fields"], limit)
        return {
            bucket: shown,
            "total": payload.get("total", len(rows)),
            "shown": len(shown),
            "timestamp": payload.get("timestamp"),
        }
    if kind == "multi":
        out = {"timestamp": payload.get("timestamp")}
        for bucket in spec["buckets"]:
            rows = payload.get(bucket) or []
            shown = _project(rows, spec["fields"], limit)
            out[bucket] = shown
            out["shown_" + bucket] = len(shown)
            out["total_" + bucket] = payload.get("total_" + bucket, len(rows))
        return out
    if kind == "flights_summary":
        buckets = ("commercial_flights", "private_flights", "private_jets", "military_flights")
        counts = {name: len(payload.get(name) or []) for name in buckets}
        sample_bucket = next((name for name in buckets if payload.get(name)), None)
        sample = _project(payload.get(sample_bucket) or [], spec["fields"], min(limit, 10))
        return {
            "counts": counts,
            "total": payload.get("total", sum(counts.values())),
            "source": payload.get("source"),
            "sample_bucket": sample_bucket,
            "sample": sample,
            "timestamp": payload.get("timestamp"),
        }
    if kind == "cables":
        bucket = spec["bucket"]
        rows = payload.get(bucket) or []
        shown = []
        for feature in rows[:limit]:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            item = {field: props[field] for field in spec["fields"] if field in props}
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") or []
            item["geometry_type"] = geometry.get("type")
            item["point_count"] = len(coords) if isinstance(coords, list) else 0
            shown.append(item)
        return {"cables": shown, "total": len(rows), "shown": len(shown)}
    if kind == "wind":
        out = {
            "refTime": payload.get("refTime"),
            "ni": payload.get("ni"),
            "nj": payload.get("nj"),
            "la1": payload.get("la1"),
            "lo1": payload.get("lo1"),
            "dx": payload.get("dx"),
            "dy": payload.get("dy"),
            "source": "NOAA GFS via /api/wind",
        }
        if lat is not None and lng is not None:
            out["sample"] = _wind_sample(payload, lat, lng)
        return out
    if kind == "rain":
        frames = payload.get("frames") or []
        times = [frame.get("time") for frame in frames if isinstance(frame, dict)]
        return {
            "host": payload.get("host"),
            "frame_count": len(frames),
            "latest_time": times[-1] if times else None,
            "paths": [frame.get("path") for frame in frames[:5] if isinstance(frame, dict)],
            "source": "RainViewer via /api/rain",
        }
    return {"error": "unsupported_kind", "kind": kind}


def _derived_dashboard(key):
    now = datetime.now(timezone.utc)
    if key == "day_night":
        day = int(now.strftime("%j"))
        declination = -23.44 * math.cos((2 * math.pi / 365) * (day + 10))
        subsolar = ((12 - (now.hour + now.minute / 60)) * 15 + 180) % 360 - 180
        return {
            "ok": True,
            "layer": key,
            "source": "client-side solar calculation",
            "url": "",
            "notes": ["No OSIRIS endpoint. The browser computes the terminator from system time."],
            "result": json.dumps({
                "utc": now.isoformat().replace("+00:00", "Z"),
                "subsolar_lat": round(declination, 2),
                "subsolar_lng": round(subsolar, 2),
            }, separators=(",", ":")),
        }
    if key == "live_clouds":
        frame = now - timedelta(minutes=20)
        frame = frame.replace(minute=0, second=0, microsecond=0)
        return {
            "ok": True,
            "layer": key,
            "source": "NOAA GMGSI WMS",
            "url": "https://nowcoast.noaa.gov/geoserver/satellite/wms",
            "notes": [
                "Keyless public imagery. This tool does not fetch or decode image tiles.",
                "The browser uses credentials='omit' for this layer.",
            ],
            "result": json.dumps({
                "frame_time": frame.isoformat().replace("+00:00", "Z"),
                "layers": {
                    "ir": "global_longwave_imagery_mosaic",
                    "vis": "global_visible_imagery_mosaic",
                },
                "coverage": "about 72.7 degrees north to south",
            }, separators=(",", ":")),
        }
    if key == "sdk_naval":
        return {
            "ok": True,
            "layer": key,
            "source": "derived OSIRIS display",
            "url": "",
            "notes": [
                "No own endpoint. The browser derives this view from news, GDELT, and earthquake data already loaded on the map.",
                "This tool does not read or write the SDK stream.",
            ],
            "result": json.dumps({
                "related_feeds": ["news", "global_incidents", "earthquakes"],
                "sdk_entity_count": None,
            }, separators=(",", ":")),
        }
    if key == "wind_controls":
        return {
            "ok": True,
            "layer": key,
            "source": "UI control",
            "url": "",
            "notes": ["No data endpoint. This control selects a GFS forecast time for the wind layer."],
            "result": json.dumps({"parent": "wind", "range": "3 days back to 5 days ahead"}, separators=(",", ":")),
        }
    if key == "rain_controls":
        return {
            "ok": True,
            "layer": key,
            "source": "UI control",
            "url": "",
            "notes": ["No data endpoint. This control selects a RainViewer radar frame for the rain layer."],
            "result": json.dumps({"parent": "rain", "range": "last 2 hours, every 10 minutes"}, separators=(",", ":")),
        }
    return None


@mcp.tool()
def osiris_lookup(kind: str, subject: str, schema: str | None = None,
                  limit: int | None = None, asset_type: str | None = None) -> dict:
    """Run a read-only OSINT lookup through the keyless OSIRIS API.

    Args:
        kind: One of dns, whois, certs, ip, shodan, portscan, bgp, mac, phone,
              github, leaks, hudsonrock, cve, sanctions, threats.
        subject: Domain, IP, CVE ID, email, username, phone number, or name.
        schema: Optional OpenSanctions entity type for sanctions (Person,
                Organization, Vessel).
        limit: Optional maximum result count for sanctions.
        asset_type: Optional hudsonrock asset type (email, domain, username, phone).
    """
    entry = LOOKUPS.get(kind.strip().lower())
    if not entry:
        return {"ok": False, "error": "unknown_kind", "available": sorted(LOOKUPS)}
    if not subject or not subject.strip():
        return {"ok": False, "error": "subject_required"}
    path, param = entry
    params = {param: subject.strip()}
    if kind == "sanctions":
        if schema:
            params["schema"] = schema
        if limit is not None:
            params["limit"] = limit
    if kind == "hudsonrock" and asset_type:
        params["type"] = asset_type
    return _fetch(path, params)


@mcp.tool()
def osiris_feed(kind: str, lat: float | None = None, lng: float | None = None,
                radius: float | None = None, days: int | None = None,
                region: str | None = None, bbox: str | None = None) -> dict:
    """Read a live OSIRIS feed or aggregate through the keyless API.

    Args:
        kind: One of stats, flights, satellites, space-weather, earthquakes,
              fires, weather, air-quality, radar, sentinel, conflicts,
              frontlines, gdelt, country-risk, region-dossier, news, live-news,
              markets, crypto, scm-suppliers, cctv, infrastructure, maritime,
              transit, geo, cyber-threats, cyber-attacks, malware, oi-trending.
        lat, lng: Optional point for sentinel, region-dossier, or cctv.
        radius: Optional radius in kilometres for sentinel or cctv.
        days: Optional lookback in days for sentinel.
        region: Optional provider region for cctv.
        bbox: Optional west,south,east,north box for transit.
    """
    path = FEEDS.get(kind.strip().lower())
    if not path:
        return {"ok": False, "error": "unknown_kind", "available": sorted(FEEDS)}
    params = {
        "lat": lat, "lng": lng, "radius": radius, "days": days,
        "region": region, "bbox": bbox,
    }
    params = {k: v for k, v in params.items() if v is not None}
    return _fetch(path, params)


@mcp.tool()
def osiris_dashboard_layer(layer: str, region: str | None = None,
                           lat: float | None = None, lng: float | None = None,
                           radius: float | None = None, run: str | None = None,
                           t: str | None = None,
                           limit: int | None = None) -> dict:
    """Read one OSIRIS dashboard layer as bounded, keyless, read-only data.

    Args:
        layer: One of flights, private, jets, military, maritime, satellites,
               cctv, cctv_previews, live_news, earthquakes, fires,
               global_incidents, day_night, cables, sdk_sea, sdk_air,
               sdk_naval, live_clouds, wind, rain, wind_controls,
               rain_controls.
        region: Required alternative to lat/lng for cctv and cctv_previews.
        lat, lng: Point filter for cctv, or a wind sample point.
        radius: Radius in kilometres for cctv. Default is 25.
        run, t: Optional UTC ISO times for one wind frame. Supply both.
        limit: Maximum rows. Default 50. Maximum 200.

    Limits:
        GET only. Fixed OSIRIS base URL. No arbitrary URL. No key or account.
        No writes. No stream proxy. No image or video fetch.
    """
    key = layer.strip().lower()
    spec = DASHBOARD_LAYERS.get(key)
    if not spec:
        derived = _derived_dashboard(key)
        if derived:
            derived["characters"] = len(derived["result"])
            derived["truncated"] = False
            return derived
        return {
            "ok": False,
            "error": "unknown_layer",
            "available": sorted(set(DASHBOARD_LAYERS) | set(DERIVED_LAYERS)),
        }

    rows = MAX_DASHBOARD_ROWS if limit is None else int(limit)
    if not 1 <= rows <= MAX_DASHBOARD_LIMIT:
        return {"ok": False, "error": "invalid_limit"}

    params = {}
    notes = []
    if spec.get("filter") == "cctv":
        checked = _cctv_params(region, lat, lng, radius)
        if "error" in checked:
            return {"ok": False, "error": checked["error"]}
        params = checked["params"]
        if "region" in params:
            notes.append("Camera metadata limited to the named provider region.")
        else:
            notes.append("Camera metadata limited by lat, lng, and radius.")
    elif key == "wind":
        if bool(run) != bool(t):
            return {"ok": False, "error": "run_and_time_required_together"}
        if run and t:
            if not ISO_UTC_RE.match(run) or not ISO_UTC_RE.match(t):
                return {"ok": False, "error": "invalid_iso_time"}
            params = {"run": run, "t": t}
        if lat is not None or lng is not None:
            if lat is None or lng is None:
                return {"ok": False, "error": "lat_and_lng_required_together"}

    data = _fetch(
        spec["path"],
        params,
        max_bytes=spec.get("max_bytes", MAX_RESPONSE_BYTES),
        truncate=False,
    )
    if not data.get("ok"):
        return dict(data, layer=key)

    payload = data["payload"]

    compact = _compact_payload(key, spec, payload, rows, lat, lng)
    notes.append("Rows are projected and capped before the result is returned.")
    if key in {"cctv", "cctv_previews"}:
        notes.append("This tool does not open, proxy, or play camera streams.")
    if key in {"day_night", "live_clouds", "wind_controls", "rain_controls"}:
        notes.append("This layer is derived or is a UI control.")
    return _bounded(key, spec["path"], data["url"], compact, notes)


if __name__ == "__main__":
    mcp.run(transport="stdio")
