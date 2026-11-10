"""Every upstream API can fail in many ways. Every tool must turn each
failure into a readable message - never a crash, hang or stack trace."""

import pytest

import fake_api as fa
import sample_data as sd
from conftest import launch

pytestmark = pytest.mark.anyio

# (tool, arguments, which upstream route is broken)
CASES = [
    ("geocode_location", {"place": "Singapore"}, fa.GEOCODE),
    ("get_current_weather", {"place": "Singapore"}, fa.GEOCODE),
    ("get_current_weather", {"place": "Singapore"}, fa.FORECAST),
    ("get_commute_advice", {"origin": "A", "destination": "B"}, fa.TRANSIT_GEOCODE),
    ("get_commute_advice", {"origin": "A", "destination": "B"}, fa.FORECAST),
    ("get_commute_advice", {"origin": "A", "destination": "B"}, fa.PLAN),
    ("get_stop_departures", {"stop": "Tampines"}, fa.TRANSIT_GEOCODE),
    ("get_stop_departures", {"stop": "83139"}, fa.STOPTIMES),
]
IDS = [f"{tool}-{route.rsplit('/', 1)[-1]}" for tool, _, route in CASES]

# Ways an API can break.
FAILURES = {
    "http500": lambda api, route: api.json(route, {"error": "boom"}, status=500),
    "http429": lambda api, route: api.json(route, {"reason": "rate"}, status=429),
    "http503-html": lambda api, route: api.raw(
        route, b"<html><h1>503 Service Unavailable</h1></html>", status=503),
    "not-json": lambda api, route: api.raw(route, b"<html>oops</html>", 200),
    "empty-body": lambda api, route: api.raw(route, b"", 200),
    "timeout": lambda api, route: api.json(route, {}, delay=1.5),
}

# Valid JSON, but not the shape we expect.
WRONG_SHAPES = {
    fa.GEOCODE: [{"results": [{"name": "No coordinates"}]}, {"results": "x"}],
    fa.FORECAST: [{}, {"current": {"time": "now"}}, []],
    fa.TRANSIT_GEOCODE: [{"unexpected": "object"}, [{"name": "no coords"}]],
    fa.PLAN: [{"itineraries": [{"legs": []}]}, {"itineraries": "x"}, []],
    fa.STOPTIMES: [{"stopTimes": [{"mode": "BUS"}]}, {"stopTimes": "x"}, []],
}


def _assert_handled(text: str):
    assert text.startswith(("Could not", "LTA DataMall request failed"))
    assert "Traceback" not in text


@pytest.mark.parametrize("failure", FAILURES)
@pytest.mark.parametrize("tool, args, route", CASES, ids=IDS)
async def test_upstream_failure_is_reported(
        client, sg_api, tool, args, route, failure):
    FAILURES[failure](sg_api, route)
    text = await client.call(tool, **args)
    _assert_handled(text)


@pytest.mark.parametrize("status", [400, 404, 429, 500, 502, 503])
async def test_http_status_appears_in_message(client, sg_api, status):
    sg_api.json(fa.FORECAST, {}, status=status)
    text = await client.call("get_current_weather", place="Singapore")
    assert str(status) in text


@pytest.mark.parametrize("tool, args, route", CASES, ids=IDS)
async def test_unexpected_json_shape_is_reported(
        client, sg_api, tool, args, route):
    for bad in WRONG_SHAPES[route]:
        sg_api.json(route, bad)
        text = await client.call(tool, **args)
        _assert_handled(text)


async def test_timeout_message_is_not_blank(client, sg_api):
    sg_api.json(fa.FORECAST, {}, delay=1.5)
    text = await client.call("get_current_weather", place="Singapore")
    detail = text.split(":", 1)[1].strip()
    assert detail, f"timeout message has no detail: {text!r}"


async def test_all_apis_unreachable(server_env, tmp_path):
    """Nothing listening on the configured port at all."""
    dead = "http://127.0.0.1:9"  # discard port: nothing listens there
    env = {**server_env,
           "OPEN_METEO_GEOCODING_URL": dead + fa.GEOCODE,
           "OPEN_METEO_FORECAST_URL": dead + fa.FORECAST,
           "TRANSITOUS_URL": dead + "/transitous/api",
           "LTA_BUS_ARRIVAL_URL": dead + fa.LTA,
           "LTA_ACCOUNT_KEY": "test-key"}
    async with launch(env, tmp_path / "server.log") as client:
        for tool, args, _route in CASES:
            _assert_handled(await client.call(tool, **args))
        _assert_handled(await client.call("get_stop_departures", stop="83139"))


async def test_server_recovers_after_failures(client, sg_api):
    sg_api.json(fa.FORECAST, {}, status=500)
    _assert_handled(await client.call("get_current_weather", place="Singapore"))
    sg_api.json(fa.FORECAST, sd.forecast())
    text = await client.call("get_current_weather", place="Singapore")
    assert "Weather in Singapore" in text
