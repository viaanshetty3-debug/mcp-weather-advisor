"""
MCP Weather & Transit Advisor
=============================
A beginner-friendly Model Context Protocol (MCP) server that gives Claude
four tools:

  1. geocode_location    - turn a place name ("Paris") into coordinates
  2. get_current_weather - real-time weather for a place name
  3. get_commute_advice  - real public-transport itineraries between two
                           places, ranked with the current weather in mind
  4. get_stop_departures - upcoming buses/trains at a stop

Data sources (all free):
  * Open-Meteo  (https://open-meteo.com)   - weather + city geocoding, no key
  * Transitous  (https://transitous.org)   - open, community-run global
    transit router built on public GTFS timetables, no key. Coverage
    depends on which agencies publish open data (Singapore is covered).
  * LTA DataMall (Singapore, optional)     - LIVE bus arrival times. Needs a
    free AccountKey from https://datamall.lta.gov.sg, supplied through the
    LTA_ACCOUNT_KEY environment variable.

Run it with:  python server.py
(Claude Desktop starts it for you - see README.md.)
"""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from mcp.server.mcpserver import MCPServer

# --------------------------------------------------------------------------
# 1. Create the server. The name is what Claude shows to the user.
# --------------------------------------------------------------------------
mcp = MCPServer("weather-transit-advisor")

# Open-Meteo endpoints (free, no key required).
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Transitous endpoints (free, no key required).
TRANSITOUS_URL = "https://api.transitous.org/api"

# LTA DataMall bus arrivals (Singapore). Only used if a key is configured.
LTA_BUS_ARRIVAL_URL = "https://datamall2.mytransport.sg/ltaodataservice/v3/BusArrival"
LTA_ACCOUNT_KEY = os.environ.get("LTA_ACCOUNT_KEY", "").strip()

# Transitous asks API users to send a descriptive User-Agent.
HEADERS = {"User-Agent": "mcp-weather-transit-advisor/0.2"}

# Never wait forever on a network call.
TIMEOUT_SECONDS = 20.0

# How many trip options to show.
MAX_ITINERARIES = 3

# Open-Meteo reports weather as numeric "WMO codes". This maps them to words.
WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Light rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# Codes that mean rain / snow / ice-or-fog / storm (used by the advisor).
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
ICE_FOG_CODES = {45, 48, 56, 57, 66, 67}
STORM_CODES = {95, 96, 99}

# LTA bus-crowding codes -> words.
LTA_LOAD = {
    "SEA": "seats available",
    "SDA": "standing available",
    "LSD": "limited standing",
}

# Friendly names for Transitous travel modes.
MODE_NAMES = {
    "WALK": "Walk", "BUS": "Bus", "SUBWAY": "Metro", "TRAM": "Tram",
    "RAIL": "Train", "REGIONAL_RAIL": "Train", "HIGHSPEED_RAIL": "Train",
    "LONG_DISTANCE": "Train", "SUBURBAN": "Train", "METRO": "Metro",
    "FERRY": "Ferry", "COACH": "Coach", "FUNICULAR": "Funicular",
    "AERIAL_LIFT": "Cable car",
}


# --------------------------------------------------------------------------
# 2. Helper functions (plain Python, not exposed to Claude)
# --------------------------------------------------------------------------
async def _get_json(url: str, params: dict, headers: dict | None = None):
    """Call an API and return its JSON. Raises a clear error on failure."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.get(
            url, params=params, headers={**HEADERS, **(headers or {})}
        )
        response.raise_for_status()  # turns HTTP 4xx/5xx into exceptions
        return response.json()


async def _find_place(name: str) -> dict:
    """Look up a CITY/TOWN name with Open-Meteo. Raises ValueError if none."""
    data = await _get_json(
        GEOCODING_URL, {"name": name, "count": 1, "language": "en"}
    )
    results = data.get("results")
    if not results:
        raise ValueError(f"No location found for '{name}'. Try a nearby city.")
    return results[0]


async def _find_transit_place(text: str, stops_only: bool = False) -> dict:
    """Look up ANY place (station, street, landmark) with Transitous.

    Returns a dict with 'name', 'lat', 'lon', 'type' and, for stops, 'id'.
    With stops_only=True, returns the best match that is a transit stop.
    Raises ValueError if nothing suitable is found.
    """
    results = await _get_json(f"{TRANSITOUS_URL}/v1/geocode", {"text": text})
    if stops_only:
        results = [r for r in results if r.get("type") == "STOP"]
    if not results:
        kind = "transit stop" if stops_only else "place"
        raise ValueError(
            f"No {kind} found for '{text}'. Add the city, e.g. "
            f"'{text}, Singapore'."
        )
    return results[0]


async def _fetch_weather(latitude: float, longitude: float) -> dict:
    """Fetch current conditions for a coordinate pair."""
    return await _get_json(
        FORECAST_URL,
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation,weather_code,wind_speed_10m,wind_gusts_10m"
            ),
            "timezone": "auto",
        },
    )


def _place_label(place: dict) -> str:
    """Build a readable label like 'Paris, Île-de-France, France'."""
    parts = [place.get("name"), place.get("admin1"), place.get("country")]
    return ", ".join(p for p in parts if p)


def _local_time(iso_string: str, tz_name: str | None) -> str:
    """Convert an ISO timestamp (often UTC) into 'HH:MM' local time."""
    moment = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
    try:
        moment = moment.astimezone(ZoneInfo(tz_name)) if tz_name else moment
    except Exception:  # unknown timezone name: keep the original offset
        pass
    return moment.strftime("%H:%M")


def _assess_weather(now: dict) -> dict:
    """Turn current conditions into warnings and simple travel ratings.

    Assumes Open-Meteo default units: degrees C, km/h, mm.
    """
    code = now["weather_code"]
    feels = now["apparent_temperature"]
    wind = now["wind_speed_10m"]
    gusts = now["wind_gusts_10m"]
    rain = now["precipitation"]

    warnings = []
    if code in STORM_CODES:
        warnings.append("Thunderstorm: avoid cycling and exposed walking.")
    if code in SNOW_CODES or code in ICE_FOG_CODES:
        warnings.append("Icy, snowy or foggy: slippery and low visibility.")
    if code in RAIN_CODES or rain > 0:
        warnings.append("Rain: bring an umbrella; roads and bike lanes are wet.")
    if gusts >= 50:
        warnings.append(f"Strong gusts ({gusts} km/h): dangerous for cyclists.")
    elif wind >= 30:
        warnings.append(f"Windy ({wind} km/h): cycling will be hard work.")
    if feels <= 0:
        warnings.append(f"Freezing (feels like {feels} C): dress warmly.")
    elif feels >= 35:
        warnings.append(f"Very hot (feels like {feels} C): stay hydrated.")

    severe = code in STORM_CODES or code in SNOW_CODES or gusts >= 50
    bad_weather = bool(warnings)
    return {
        "summary": f"{WEATHER_CODES.get(code, 'Unknown')}, feels like "
                   f"{feels} C, wind {wind} km/h",
        "warnings": warnings,
        "bad_weather": bad_weather,
        "walking": "Poor" if severe else ("Fair" if bad_weather else "Good"),
        "cycling": "Poor" if severe or wind >= 30 else (
            "Fair" if bad_weather else "Good"),
    }


def _walk_minutes(itinerary: dict) -> int:
    """Total minutes spent walking in one itinerary."""
    seconds = sum(
        leg.get("duration", 0)
        for leg in itinerary["legs"]
        if leg["mode"] == "WALK"
    )
    return round(seconds / 60)


def _describe_itinerary(itinerary: dict, tz_name: str | None) -> list[str]:
    """Turn one Transitous itinerary into readable lines."""
    start = _local_time(itinerary["startTime"], tz_name)
    end = _local_time(itinerary["endTime"], tz_name)
    minutes = round(itinerary["duration"] / 60)
    lines = [
        f"{start} -> {end} ({minutes} min, "
        f"{itinerary.get('transfers', 0)} transfer(s), "
        f"{_walk_minutes(itinerary)} min walking)"
    ]
    for leg in itinerary["legs"]:
        mode = MODE_NAMES.get(leg["mode"], leg["mode"].title())
        leg_minutes = round(leg.get("duration", 0) / 60)
        if leg["mode"] == "WALK":
            if leg_minutes == 0:
                continue  # skip zero-length transfer walks
            lines.append(f"   - Walk {leg_minutes} min to {leg['to']['name']}")
            continue
        route = leg.get("routeShortName") or ""
        toward = f" towards {leg['headsign']}" if leg.get("headsign") else ""
        live = "live" if leg.get("realTime") else "scheduled"
        lines.append(
            f"   - {mode} {route}{toward}: {leg['from']['name']} "
            f"{_local_time(leg['startTime'], tz_name)} ({live}) -> "
            f"{leg['to']['name']} {_local_time(leg['endTime'], tz_name)}"
        )
    return lines


# --------------------------------------------------------------------------
# 3. Tools - these are what Claude can call.
#    The docstring is shown to Claude, so it explains WHEN to use the tool.
# --------------------------------------------------------------------------
@mcp.tool()
async def geocode_location(place: str) -> str:
    """Find the coordinates (latitude/longitude) of a city or town by name.

    Args:
        place: A city or town name, e.g. "Singapore" or "Austin".
    """
    try:
        found = await _find_place(place)
    except (ValueError, httpx.HTTPError) as err:
        return f"Could not geocode '{place}': {err}"

    return (
        f"{_place_label(found)}\n"
        f"Latitude: {found['latitude']}\n"
        f"Longitude: {found['longitude']}\n"
        f"Timezone: {found.get('timezone', 'unknown')}"
    )


@mcp.tool()
async def get_current_weather(place: str) -> str:
    """Get real-time weather for a city or town by name.

    Args:
        place: A city or town name, e.g. "Tokyo".
    """
    try:
        found = await _find_place(place)
        weather = await _fetch_weather(found["latitude"], found["longitude"])
    except (ValueError, httpx.HTTPError) as err:
        return f"Could not get weather for '{place}': {err}"

    now = weather["current"]
    units = weather["current_units"]
    conditions = WEATHER_CODES.get(now["weather_code"], "Unknown")

    return (
        f"Weather in {_place_label(found)} (as of {now['time']} local time)\n"
        f"Conditions: {conditions}\n"
        f"Temperature: {now['temperature_2m']}{units['temperature_2m']} "
        f"(feels like {now['apparent_temperature']}"
        f"{units['apparent_temperature']})\n"
        f"Humidity: {now['relative_humidity_2m']}{units['relative_humidity_2m']}\n"
        f"Precipitation: {now['precipitation']} {units['precipitation']}\n"
        f"Wind: {now['wind_speed_10m']} {units['wind_speed_10m']} "
        f"(gusts {now['wind_gusts_10m']} {units['wind_gusts_10m']})"
    )


@mcp.tool()
async def get_commute_advice(origin: str, destination: str) -> str:
    """Plan a public-transport trip leaving now, using real timetables,
    and recommend an option based on the current weather.

    Uses open GTFS timetable data via Transitous (live delays where the
    operator publishes them). Coverage depends on the region.

    Args:
        origin: Where you start - a station, landmark or address,
            e.g. "Orchard MRT, Singapore".
        destination: Where you are going, e.g. "Changi Airport".
    """
    try:
        start = await _find_transit_place(origin)
        end = await _find_transit_place(destination)
        weather = await _fetch_weather(start["lat"], start["lon"])
        plan = await _get_json(
            f"{TRANSITOUS_URL}/v5/plan",
            {
                "fromPlace": f"{start['lat']},{start['lon']}",
                "toPlace": f"{end['lat']},{end['lon']}",
                "numItineraries": MAX_ITINERARIES,
            },
        )
    except (ValueError, httpx.HTTPError) as err:
        return f"Could not plan a trip from '{origin}' to '{destination}': {err}"

    tz_name = weather.get("timezone")
    assessment = _assess_weather(weather["current"])
    itineraries = plan.get("itineraries", [])[:MAX_ITINERARIES]

    lines = [
        f"Trip: {start['name']} -> {end['name']}",
        f"Weather at start: {assessment['summary']}",
        f"Walking: {assessment['walking']} | Cycling: {assessment['cycling']}",
    ]
    lines += [f"- {w}" for w in assessment["warnings"]] or [
        "- No weather concerns."]

    if not itineraries:
        lines.append(
            "\nNo public-transport route found. The region may not publish "
            "open timetable data, or the places may be too far apart."
        )
        return "\n".join(lines)

    # Weather-aware pick: in bad weather prefer the least walking,
    # otherwise prefer the earliest arrival.
    if assessment["bad_weather"]:
        best = min(itineraries, key=lambda it: (_walk_minutes(it), it["endTime"]))
        reason = "least walking, because of the weather"
    else:
        best = min(itineraries, key=lambda it: it["endTime"])
        reason = "earliest arrival"

    lines.append(f"\nRecommended ({reason}):")
    lines += _describe_itinerary(best, tz_name)

    others = [it for it in itineraries if it is not best]
    if others:
        lines.append("\nOther options:")
        for it in others:
            lines += _describe_itinerary(it, tz_name)

    lines.append("\n(Transit data: Transitous / open GTFS feeds. "
                 "'scheduled' times are timetabled, not live.)")
    return "\n".join(lines)


@mcp.tool()
async def get_stop_departures(stop: str, limit: int = 8) -> str:
    """List upcoming buses/trains at a public-transport stop.

    For a Singapore 5-digit bus stop code (e.g. "83139") this returns LIVE
    arrivals from LTA DataMall when an LTA key is configured; otherwise it
    returns timetabled departures from Transitous.

    Args:
        stop: A stop name ("Tampines MRT, Singapore") or a Singapore
            5-digit bus stop code ("83139").
        limit: Maximum number of departures to list (default 8).
    """
    limit = max(1, min(limit, 20))
    is_sg_code = stop.isdigit() and len(stop) == 5

    # --- Live path: Singapore LTA DataMall --------------------------------
    if is_sg_code and LTA_ACCOUNT_KEY:
        try:
            data = await _get_json(
                LTA_BUS_ARRIVAL_URL,
                {"BusStopCode": stop},
                headers={"AccountKey": LTA_ACCOUNT_KEY,
                         "accept": "application/json"},
            )
        except httpx.HTTPError as err:
            return f"LTA DataMall request failed for stop {stop}: {err}"
        return _format_lta_arrivals(stop, data, limit)

    # --- Timetable path: Transitous ---------------------------------------
    try:
        if is_sg_code:
            stop_id = f"sg-LTA_{stop}"  # Transitous ID for SG bus stops
        else:
            found = await _find_transit_place(stop, stops_only=True)
            stop_id = found["id"]
        data = await _get_json(
            f"{TRANSITOUS_URL}/v5/stoptimes", {"stopId": stop_id, "n": limit}
        )
    except (ValueError, httpx.HTTPError) as err:
        return f"Could not get departures for '{stop}': {err}"

    # Transitous treats 'n' as a target, not a cap, so trim to the limit.
    stop_times = data.get("stopTimes", [])[:limit]
    if not stop_times:
        return f"No upcoming departures found for '{stop}'."

    tz_name = data.get("place", {}).get("tz") or stop_times[0]["place"].get("tz")
    lines = [f"Departures from {stop_times[0]['place']['name']}:"]
    for st in stop_times:
        mode = MODE_NAMES.get(st["mode"], st["mode"].title())
        when = st["place"].get("departure") or st["place"].get("arrival")
        live = "live" if st.get("realTime") else "scheduled"
        lines.append(
            f"- {_local_time(when, tz_name)} {mode} {st.get('routeShortName', '')}"
            f" towards {st.get('headsign', '?')} ({live})"
        )
    if is_sg_code:
        lines.append("(Timetabled only. Set LTA_ACCOUNT_KEY for live arrivals.)")
    return "\n".join(lines)


def _format_lta_arrivals(stop: str, data: dict, limit: int) -> str:
    """Format an LTA DataMall v3 BusArrival response."""
    services = data.get("Services", [])
    if not services:
        return f"No buses currently serving stop {stop} (LTA DataMall)."

    now = datetime.now(timezone.utc)
    lines = [f"Live bus arrivals at stop {stop} (LTA DataMall):"]
    for service in services[:limit]:
        etas = []
        for key in ("NextBus", "NextBus2", "NextBus3"):
            bus = service.get(key) or {}
            if not bus.get("EstimatedArrival"):
                continue
            eta = datetime.fromisoformat(bus["EstimatedArrival"])
            minutes = max(0, round((eta - now).total_seconds() / 60))
            text = "Arr" if minutes == 0 else f"{minutes} min"
            load = LTA_LOAD.get(bus.get("Load", ""))
            if load:
                text += f" ({load})"
            if not bus.get("Monitored"):
                text += " [scheduled]"
            etas.append(text)
        lines.append(f"- Bus {service['ServiceNo']}: "
                     f"{', '.join(etas) if etas else 'no estimate'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 4. Start the server. "stdio" means Claude talks to it through the
#    program's input/output, which is how Claude Desktop launches servers.
# --------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
