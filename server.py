"""
MCP Weather & Commute Advisor
=============================
A beginner-friendly Model Context Protocol (MCP) server that gives Claude
three tools:

  1. geocode_location   - turn a place name ("Paris") into coordinates
  2. get_current_weather - real-time weather for a place name
  3. get_commute_advice  - weather-based advice on walking / cycling / transit

Data comes from Open-Meteo (https://open-meteo.com), which is free and
needs NO API key.

Note on "transit": Open-Meteo has no bus/train schedules. The commute tool
uses live WEATHER conditions to advise on how comfortable and safe each way
of travelling is. It does not know timetables or routes.

Run it with:  python server.py
(Claude Desktop starts it for you - see README.md.)
"""

import httpx
from mcp.server.mcpserver import MCPServer

# --------------------------------------------------------------------------
# 1. Create the server. The name is what Claude shows to the user.
# --------------------------------------------------------------------------
mcp = MCPServer("weather-transit-advisor")

# Open-Meteo endpoints (both free, no key required).
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Never wait forever on a network call.
TIMEOUT_SECONDS = 10.0

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

# Codes that mean rain / snow / storm (used by the commute advisor).
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
STORM_CODES = {95, 96, 99}


# --------------------------------------------------------------------------
# 2. Helper functions (plain Python, not exposed to Claude)
# --------------------------------------------------------------------------
async def _get_json(url: str, params: dict) -> dict:
    """Call an API and return its JSON. Raises a clear error on failure."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()  # turns HTTP 4xx/5xx into exceptions
        return response.json()


async def _find_place(name: str) -> dict:
    """Look up a place name and return its best match.

    Raises ValueError if nothing is found.
    """
    data = await _get_json(
        GEOCODING_URL, {"name": name, "count": 1, "language": "en"}
    )
    results = data.get("results")
    if not results:
        raise ValueError(f"No location found for '{name}'. Try a nearby city.")
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


# --------------------------------------------------------------------------
# 3. Tools - these are what Claude can call.
#    The docstring is shown to Claude, so it explains WHEN to use the tool.
# --------------------------------------------------------------------------
@mcp.tool()
async def geocode_location(place: str) -> str:
    """Find the coordinates (latitude/longitude) of a place by name.

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
    """Get real-time weather for a place by name.

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
async def get_commute_advice(place: str) -> str:
    """Advise how weather affects walking, cycling and public transit now.

    This is based on live weather only - it has no timetable or route data.

    Args:
        place: A city or town name, e.g. "London".
    """
    try:
        found = await _find_place(place)
        weather = await _fetch_weather(found["latitude"], found["longitude"])
    except (ValueError, httpx.HTTPError) as err:
        return f"Could not build commute advice for '{place}': {err}"

    now = weather["current"]
    code = now["weather_code"]
    feels = now["apparent_temperature"]   # degrees C (API default)
    wind = now["wind_speed_10m"]          # km/h (API default)
    gusts = now["wind_gusts_10m"]         # km/h
    rain = now["precipitation"]           # mm

    # Collect a plain-English warning for each problem we detect.
    warnings = []
    if code in STORM_CODES:
        warnings.append("Thunderstorm: avoid cycling and exposed walking.")
    if code in SNOW_CODES or code in {66, 67, 56, 57, 45, 48}:
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

    # Simple rule-of-thumb ratings per mode of travel.
    severe = code in STORM_CODES or code in SNOW_CODES or gusts >= 50
    wet_or_extreme = bool(warnings)
    walking = "Poor" if severe else ("Fair" if wet_or_extreme else "Good")
    cycling = "Poor" if severe or wind >= 30 else (
        "Fair" if wet_or_extreme else "Good")
    transit = "Recommended" if wet_or_extreme else "Fine"

    lines = [
        f"Commute advice for {_place_label(found)}",
        f"Now: {WEATHER_CODES.get(code, 'Unknown')}, feels like {feels} C, "
        f"wind {wind} km/h",
        f"Walking: {walking} | Cycling: {cycling} | Public transit: {transit}",
    ]
    lines += [f"- {w}" for w in warnings] or ["- No weather concerns."]
    lines.append("(Weather-based only; check your operator for live schedules.)")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 4. Start the server. "stdio" means Claude talks to it through the
#    program's input/output, which is how Claude Desktop launches servers.
# --------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
