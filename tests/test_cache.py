"""SQLite cache behaviour, tested end-to-end over stdio.

Each test launches its own server with a fresh cache file.
"""

import sqlite3

import anyio
import pytest

import fake_api as fa
import sample_data as sd
from conftest import launch

pytestmark = pytest.mark.anyio

CACHED = "Served from cache"
SOON = 2 / 60       # minutes: a trip leaving 2 seconds after the request
WAIT = 3            # seconds to sleep so that trip has departed


@pytest.fixture
def cache_file(tmp_path):
    return tmp_path / "cache.sqlite3"


@pytest.fixture
def cached_server(server_env, cache_file, tmp_path):
    def _start(**extra):
        env = {**server_env, "WEATHER_ADVISOR_CACHE": str(cache_file), **extra}
        return launch(env, tmp_path / "server.log")
    return _start


def rows(cache_file, pattern="%"):
    with sqlite3.connect(cache_file) as db:
        return db.execute(
            "SELECT key, body, fetched_at, expires_at FROM responses "
            "WHERE key LIKE ?", (pattern,)).fetchall()


# ---- hits and misses --------------------------------------------------------
async def test_repeat_weather_is_served_from_cache(cached_server, sg_api):
    async with cached_server() as client:
        first = await client.call("get_current_weather", place="Singapore")
        second = await client.call("get_current_weather", place="Singapore")
    assert CACHED not in first
    assert CACHED in second and "fetched <1 min ago" in second
    assert len(sg_api.calls(fa.FORECAST)) == 1
    assert len(sg_api.calls(fa.GEOCODE)) == 1
    # Same data either way.
    assert first == second.split("\n(" + CACHED)[0]


async def test_differently_typed_city_shares_cache(cached_server, sg_api):
    async with cached_server() as client:
        for spelling in ["Singapore", "  SINGAPORE ", "singapore"]:
            await client.call("get_current_weather", place=spelling)
    assert len(sg_api.calls(fa.GEOCODE)) == 1
    assert len(sg_api.calls(fa.FORECAST)) == 1


async def test_different_cities_are_cached_separately(cached_server, sg_api):
    sg_api.dynamic(fa.GEOCODE, lambda q: sd.geocode_response(
        sd.city(q["name"].title(), lat=len(q["name"]), lon=1)))
    async with cached_server() as client:
        await client.call("get_current_weather", place="Oslo")
        text = await client.call("get_current_weather", place="Lisbon")
    assert CACHED not in text
    assert len(sg_api.calls(fa.FORECAST)) == 2


async def test_repeat_commute_is_served_from_cache(cached_server, sg_api):
    args = {"origin": "Orchard", "destination": "Changi"}
    async with cached_server() as client:
        first = await client.call("get_commute_advice", **args)
        second = await client.call("get_commute_advice", **args)
    assert CACHED in second
    assert len(sg_api.calls(fa.PLAN)) == 1
    assert first == second.split("\n(" + CACHED)[0]


async def test_departures_share_cache_across_limits(cached_server, sg_api):
    async with cached_server() as client:
        await client.call("get_stop_departures", stop="83139", limit=1)
        text = await client.call("get_stop_departures", stop="83139", limit=3)
    assert len(sg_api.calls(fa.STOPTIMES)) == 1
    assert CACHED in text


async def test_cache_disabled_always_calls_api(cached_server, sg_api):
    async with cached_server(WEATHER_ADVISOR_CACHE="off") as client:
        for _ in range(3):
            text = await client.call("get_current_weather", place="Singapore")
    assert CACHED not in text
    assert len(sg_api.calls(fa.FORECAST)) == 3


# ---- expiry and freshness ---------------------------------------------------
async def test_ttls_written_to_cache(cached_server, sg_api, cache_file):
    async with cached_server(LTA_ACCOUNT_KEY="test-key") as client:
        await client.call("get_current_weather", place="Singapore")
        await client.call("get_commute_advice", origin="A", destination="B")
        await client.call("get_stop_departures", stop="83139")
    ttl = {pattern: round(r[3] - r[2]) for pattern in
           ["%/forecast%", "%/v5/plan%", "%BusArrival%", "%/v1/search%",
            "%/v1/geocode%"] for r in rows(cache_file, pattern)}
    assert ttl == {"%/forecast%": 900, "%/v5/plan%": 900,
                   "%BusArrival%": 60, "%/v1/search%": 604800,
                   "%/v1/geocode%": 604800}


async def test_expired_entry_is_refetched(cached_server, sg_api, cache_file):
    async with cached_server() as client:
        await client.call("get_current_weather", place="Singapore")
        with sqlite3.connect(cache_file) as db:
            db.execute("UPDATE responses SET expires_at = 0 "
                       "WHERE key LIKE '%forecast%'")
        text = await client.call("get_current_weather", place="Singapore")
    assert CACHED not in text
    assert len(sg_api.calls(fa.FORECAST)) == 2
    assert len(sg_api.calls(fa.GEOCODE)) == 1  # geocode still cached


async def test_cache_age_is_reported_in_minutes(cached_server, sg_api,
                                                cache_file):
    async with cached_server() as client:
        await client.call("get_current_weather", place="Singapore")
        with sqlite3.connect(cache_file) as db:
            db.execute("UPDATE responses SET fetched_at = fetched_at - 420")
        text = await client.call("get_current_weather", place="Singapore")
    assert "fetched 7 min ago" in text


async def test_cached_plan_hides_departed_trips(cached_server, sg_api):
    # Times are generated when the request arrives: SOON leaves in ~2 s.
    sg_api.dynamic(fa.PLAN, lambda q: sd.plan(
        sd.itinerary(SOON, [sd.ride(SOON, 5, route="SOON")]),
        sd.itinerary(30, [sd.ride(30, 5, route="LATER")])))
    args = {"origin": "A", "destination": "B"}
    async with cached_server() as client:
        first = await client.call("get_commute_advice", **args)
        await anyio.sleep(WAIT)
        second = await client.call("get_commute_advice", **args)
    assert "Bus SOON" in first
    assert "Bus SOON" not in second and "Bus LATER" in second
    assert CACHED in second
    assert len(sg_api.calls(fa.PLAN)) == 1  # a trip was left: no refetch


async def test_cached_plan_refetched_when_all_trips_departed(
        cached_server, sg_api):
    sg_api.dynamic(fa.PLAN, lambda q: sd.plan(
        sd.itinerary(SOON, [sd.ride(SOON, 5, route="SOON")])))
    args = {"origin": "A", "destination": "B"}
    async with cached_server() as client:
        await client.call("get_commute_advice", **args)
        await anyio.sleep(WAIT)
        sg_api.json(fa.PLAN, sd.plan(
            sd.itinerary(10, [sd.ride(10, 5, route="FRESH")])))
        text = await client.call("get_commute_advice", **args)
    assert "Bus FRESH" in text
    assert len(sg_api.calls(fa.PLAN)) == 2


async def test_cached_departures_refetched_when_too_few_left(
        cached_server, sg_api):
    sg_api.dynamic(fa.STOPTIMES, lambda q: sd.stoptimes(
        sd.stoptime(SOON, "SOON"), sd.stoptime(30, "LATER")))
    async with cached_server() as client:
        await client.call("get_stop_departures", stop="83139", limit=2)
        await anyio.sleep(WAIT)
        sg_api.json(fa.STOPTIMES, sd.stoptimes(
            sd.stoptime(5, "A"), sd.stoptime(6, "B")))
        text = await client.call("get_stop_departures", stop="83139", limit=2)
    assert "Bus A" in text and "Bus B" in text
    assert len(sg_api.calls(fa.STOPTIMES)) == 2


# ---- safety -----------------------------------------------------------------
async def test_errors_are_not_cached(cached_server, sg_api, cache_file):
    sg_api.json(fa.FORECAST, {}, status=503)
    async with cached_server() as client:
        await client.call("get_current_weather", place="Singapore")
        sg_api.json(fa.FORECAST, sd.forecast())
        text = await client.call("get_current_weather", place="Singapore")
    assert "Weather in Singapore" in text and CACHED not in text
    assert len(sg_api.calls(fa.FORECAST)) == 2


async def test_not_found_lookups_are_not_cached(cached_server, sg_api):
    """A place that didn't exist a moment ago shouldn't stay 'not found'
    for 7 days. (Empty geocoder answers must not be cached.)"""
    sg_api.json(fa.GEOCODE, sd.geocode_response())
    async with cached_server() as client:
        await client.call("get_current_weather", place="Singapore")
        sg_api.json(fa.GEOCODE, sd.geocode_response(sd.city()))
        text = await client.call("get_current_weather", place="Singapore")
    assert "Weather in Singapore" in text


async def test_api_key_is_never_written_to_cache(cached_server, sg_api,
                                                 cache_file):
    async with cached_server(LTA_ACCOUNT_KEY="super-secret-key") as client:
        await client.call("get_stop_departures", stop="83139")
    assert rows(cache_file, "%BusArrival%")
    assert b"super-secret-key" not in cache_file.read_bytes()


async def test_cache_survives_server_restart(cached_server, sg_api):
    async with cached_server() as client:
        await client.call("get_current_weather", place="Singapore")
    async with cached_server() as client:
        text = await client.call("get_current_weather", place="Singapore")
    assert CACHED in text
    assert len(sg_api.calls(fa.FORECAST)) == 1


async def test_corrupt_cache_file_falls_back_to_api(cached_server, sg_api,
                                                    cache_file):
    cache_file.write_bytes(b"this is not a sqlite database" * 100)
    async with cached_server() as client:
        for _ in range(2):
            text = await client.call("get_current_weather", place="Singapore")
    assert "Weather in Singapore" in text


async def test_unwritable_cache_location_falls_back_to_api(
        cached_server, sg_api, tmp_path):
    bad = tmp_path / "missing-dir" / "cache.sqlite3"
    async with cached_server(WEATHER_ADVISOR_CACHE=str(bad)) as client:
        text = await client.call("get_current_weather", place="Singapore")
    assert "Weather in Singapore" in text


async def test_corrupt_cached_row_is_ignored(cached_server, sg_api,
                                             cache_file):
    async with cached_server() as client:
        await client.call("get_current_weather", place="Singapore")
        with sqlite3.connect(cache_file) as db:
            db.execute("UPDATE responses SET body = '{not json'")
        text = await client.call("get_current_weather", place="Singapore")
    assert "Weather in Singapore" in text
