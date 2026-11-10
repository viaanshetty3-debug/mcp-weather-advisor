"""Builders for realistic API responses, shaped like the real services.

Times are generated relative to "now" so trips and departures are always
in the future (or deliberately in the past) whenever the tests run.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SG_TZ = "Asia/Singapore"


def iso_utc(minutes_from_now: float) -> str:
    """ISO timestamp like Transitous returns: UTC with a trailing Z."""
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_sg(minutes_from_now: float) -> str:
    """ISO timestamp like LTA returns: Singapore local time with offset."""
    t = datetime.now(ZoneInfo(SG_TZ)) + timedelta(minutes=minutes_from_now)
    return t.isoformat(timespec="seconds")


def local_hhmm(iso: str, tz: str = SG_TZ) -> str:
    """What the server should print for an ISO time in a timezone."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return t.astimezone(ZoneInfo(tz)).strftime("%H:%M")


# ---- Open-Meteo -------------------------------------------------------------
def city(name="Singapore", lat=1.28967, lon=103.85007, country="Singapore",
         admin1=None, tz=SG_TZ) -> dict:
    result = {"name": name, "latitude": lat, "longitude": lon,
              "country": country, "timezone": tz}
    if admin1:
        result["admin1"] = admin1
    return result


def geocode_response(*cities) -> dict:
    # Open-Meteo omits "results" entirely when nothing matches.
    return {"results": list(cities)} if cities else {"generationtime_ms": 0.4}


def forecast(code=3, temp=29.1, feels=31.0, humidity=74, precip=0.0,
             wind=6.8, gusts=20.2, tz=SG_TZ) -> dict:
    return {
        "timezone": tz,
        "current": {
            "time": "2026-11-10T10:45", "temperature_2m": temp,
            "apparent_temperature": feels, "relative_humidity_2m": humidity,
            "precipitation": precip, "weather_code": code,
            "wind_speed_10m": wind, "wind_gusts_10m": gusts,
        },
        "current_units": {
            "time": "iso8601", "temperature_2m": "°C",
            "apparent_temperature": "°C", "relative_humidity_2m": "%",
            "precipitation": "mm", "wind_speed_10m": "km/h",
            "wind_gusts_10m": "km/h",
        },
    }


# ---- Transitous -------------------------------------------------------------
def transit_place(name, lat=1.30, lon=103.83, type_="STOP", id_=None) -> dict:
    place = {"type": type_, "name": name, "lat": lat, "lon": lon}
    if type_ == "STOP":
        place["id"] = id_ or f"sg-LTA_{name.upper().replace(' ', '_')}"
    return place


def walk(start_min, minutes, to="Stop"):
    return {"mode": "WALK", "duration": minutes * 60,
            "startTime": iso_utc(start_min),
            "endTime": iso_utc(start_min + minutes),
            "from": {"name": "START"}, "to": {"name": to}}


def ride(start_min, minutes, route="106", mode="BUS", frm="Orchard Stn",
         to="Bayfront Stn", headsign="Shenton Way", realtime=False):
    return {"mode": mode, "duration": minutes * 60,
            "startTime": iso_utc(start_min),
            "endTime": iso_utc(start_min + minutes),
            "from": {"name": frm}, "to": {"name": to},
            "routeShortName": route, "headsign": headsign,
            "realTime": realtime}


def itinerary(start_min, legs, transfers=0) -> dict:
    """An itinerary whose start/end/duration are derived from its legs."""
    end_min = start_min + sum(leg["duration"] for leg in legs) / 60
    return {"startTime": iso_utc(start_min), "endTime": iso_utc(end_min),
            "duration": int((end_min - start_min) * 60),
            "transfers": transfers, "legs": legs}


def fast_but_walky(start_min=2):
    """Arrives first, but involves 15 minutes of walking."""
    return itinerary(start_min, [
        walk(start_min, 10, "Orchard Stn"),
        ride(start_min + 10, 10, route="NS", mode="SUBWAY"),
        walk(start_min + 20, 5, "END")])


def slow_but_dry(start_min=3):
    """Arrives later, but only 1 minute of walking."""
    return itinerary(start_min, [
        walk(start_min, 1, "Delfi Orchard"),
        ride(start_min + 1, 40, route="7", frm="Delfi Orchard")],
        transfers=1)


def plan(*itineraries) -> dict:
    return {"itineraries": list(itineraries)}


def stoptime(minutes_from_now, route="15", headsign="Pasir Ris Int",
             mode="BUS", name="Aft Joo Chiat Pl", realtime=False) -> dict:
    when = iso_utc(minutes_from_now)
    return {"place": {"name": name, "departure": when, "arrival": when,
                      "tz": SG_TZ},
            "mode": mode, "routeShortName": route, "headsign": headsign,
            "realTime": realtime}


def stoptimes(*entries) -> dict:
    return {"stopTimes": list(entries), "place": {"tz": SG_TZ}}


# ---- LTA DataMall -----------------------------------------------------------
def lta_bus(minutes_from_now, load="SEA", monitored=1) -> dict:
    return {"EstimatedArrival": iso_sg(minutes_from_now),
            "Monitored": monitored, "Load": load}


def lta_service(number, *buses) -> dict:
    service = {"ServiceNo": number, "Operator": "SBST"}
    for key, bus in zip(("NextBus", "NextBus2", "NextBus3"), buses):
        service[key] = bus
    return service


def lta_response(*services, stop="83139") -> dict:
    return {"BusStopCode": stop, "Services": list(services)}
