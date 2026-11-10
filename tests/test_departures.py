"""get_stop_departures over stdio: Transitous timetables and LTA live data."""

import re

import pytest

import fake_api as fa
import sample_data as sd
from conftest import launch

pytestmark = pytest.mark.anyio


def departure_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("- ")]


# ---- Transitous timetable path (no LTA key) ---------------------------------
async def test_singapore_code_uses_transitous_stop_id(client, sg_api):
    text = await client.call("get_stop_departures", stop="83139")
    query = sg_api.calls(fa.STOPTIMES)[0].query
    assert query["stopId"] == "sg-LTA_83139"
    assert not sg_api.calls(fa.TRANSIT_GEOCODE)  # no search needed
    assert "Departures from Aft Joo Chiat Pl:" in text
    assert "Set LTA_ACCOUNT_KEY for live arrivals" in text


async def test_code_with_surrounding_spaces_is_still_a_code(client, sg_api):
    await client.call("get_stop_departures", stop=" 83139 ")
    assert sg_api.calls(fa.STOPTIMES)[0].query["stopId"] == "sg-LTA_83139"
    assert not sg_api.calls(fa.TRANSIT_GEOCODE)  # recognised as a code


@pytest.mark.parametrize("not_a_code", ["8313", "831390", "83a39"])
async def test_non_five_digit_input_is_searched_by_name(
        client, sg_api, not_a_code):
    await client.call("get_stop_departures", stop=not_a_code)
    assert sg_api.calls(fa.TRANSIT_GEOCODE)[0].query["text"] == not_a_code


async def test_departure_lines_are_formatted(client, sg_api):
    entry = sd.stoptime(7, "155", "Bedok Int", realtime=True)
    sg_api.json(fa.STOPTIMES, sd.stoptimes(entry))
    text = await client.call("get_stop_departures", stop="83139")
    when = sd.local_hhmm(entry["place"]["departure"])
    assert departure_lines(text) == [f"- {when} Bus 155 towards Bedok Int (live)"]


async def test_stop_name_picks_first_stop_not_landmark(client, sg_api):
    sg_api.json(fa.TRANSIT_GEOCODE, [
        sd.transit_place("Marina Bay Sands", type_="PLACE"),
        sd.transit_place("Bayfront Stn", id_="sg-LTA_03509"),
        sd.transit_place("Other Stop", id_="sg-LTA_99999")])
    await client.call("get_stop_departures", stop="Marina Bay Sands")
    assert sg_api.calls(fa.STOPTIMES)[0].query["stopId"] == "sg-LTA_03509"


async def test_name_matching_no_stop(client, sg_api):
    sg_api.json(fa.TRANSIT_GEOCODE, [
        sd.transit_place("Some Mall", type_="PLACE")])
    text = await client.call("get_stop_departures", stop="Some Mall")
    assert "No transit stop found for 'Some Mall'" in text
    assert not sg_api.calls(fa.STOPTIMES)


async def test_blank_stop_is_rejected(client, sg_api):
    text = await client.call("get_stop_departures", stop="  ")
    assert "provide" in text.lower()
    assert not sg_api.requests


@pytest.mark.parametrize("limit, shown", [
    (0, 1), (-5, 1), (1, 1), (3, 3), (8, 8), (100, 20)])
async def test_limit_is_clamped_to_1_through_20(client, sg_api, limit, shown):
    sg_api.json(fa.STOPTIMES, sd.stoptimes(
        *[sd.stoptime(m + 1, str(m)) for m in range(25)]))
    text = await client.call("get_stop_departures", stop="83139", limit=limit)
    assert len(departure_lines(text)) == shown


async def test_departed_entries_are_dropped(client, sg_api):
    sg_api.json(fa.STOPTIMES, sd.stoptimes(
        sd.stoptime(-3, "OLD"), sd.stoptime(4, "NEW")))
    text = await client.call("get_stop_departures", stop="83139")
    assert "Bus NEW" in text and "Bus OLD" not in text


async def test_no_upcoming_departures(client, sg_api):
    sg_api.json(fa.STOPTIMES, sd.stoptimes())
    text = await client.call("get_stop_departures", stop="83139")
    assert "No upcoming departures found for '83139'" in text


async def test_arrival_time_used_when_no_departure(client, sg_api):
    entry = sd.stoptime(5, "TERM")
    del entry["place"]["departure"]  # e.g. last stop of a route
    sg_api.json(fa.STOPTIMES, sd.stoptimes(entry))
    text = await client.call("get_stop_departures", stop="83139")
    assert "Bus TERM" in text


async def test_requests_fixed_departure_count(client, sg_api):
    """Fixed n means all limits share one cache entry."""
    await client.call("get_stop_departures", stop="83139", limit=3)
    assert sg_api.calls(fa.STOPTIMES)[0].query["n"] == "20"


# ---- LTA DataMall live path (dedicated server with a key) -------------------
@pytest.fixture(scope="module")
async def lta_client(server_env, tmp_path_factory):
    log = tmp_path_factory.mktemp("lta") / "server.log"
    async with launch({**server_env, "LTA_ACCOUNT_KEY": "test-key"}, log) as c:
        yield c


async def test_lta_sends_key_and_formats_arrivals(lta_client, sg_api):
    sg_api.json(fa.LTA, sd.lta_response(
        sd.lta_service("15", sd.lta_bus(3.5, "SEA"), sd.lta_bus(12.5, "SDA"),
                       sd.lta_bus(20.5, "LSD", monitored=0))))
    text = await lta_client.call("get_stop_departures", stop="83139")
    request = sg_api.calls(fa.LTA)[0]
    assert request.headers["AccountKey"] == "test-key"
    assert request.query == {"BusStopCode": "83139"}
    assert not sg_api.calls(fa.STOPTIMES)
    assert text.startswith("Live bus arrivals at stop 83139 (LTA DataMall):")
    line = departure_lines(text)[0]
    assert re.fullmatch(
        r"- Bus 15: \d+ min \(seats available\), \d+ min \(standing "
        r"available\), \d+ min \(limited standing\) \[scheduled\]", line), line


async def test_lta_bus_arriving_now_or_late_shows_arr(lta_client, sg_api):
    sg_api.json(fa.LTA, sd.lta_response(
        sd.lta_service("10", sd.lta_bus(0.1)),
        sd.lta_service("20", sd.lta_bus(-2))))   # ETA already passed
    text = await lta_client.call("get_stop_departures", stop="83139")
    assert "- Bus 10: Arr" in text and "- Bus 20: Arr" in text


async def test_lta_missing_estimates(lta_client, sg_api):
    sg_api.json(fa.LTA, sd.lta_response(sd.lta_service(
        "36", {"EstimatedArrival": ""}, {}, {})))
    text = await lta_client.call("get_stop_departures", stop="83139")
    assert "- Bus 36: no estimate" in text


async def test_lta_unknown_load_code_is_omitted(lta_client, sg_api):
    sg_api.json(fa.LTA, sd.lta_response(
        sd.lta_service("36", sd.lta_bus(5, load=""))))
    text = await lta_client.call("get_stop_departures", stop="83139")
    assert re.search(r"- Bus 36: \d+ min$", text, re.M)


async def test_lta_no_services(lta_client, sg_api):
    sg_api.json(fa.LTA, sd.lta_response())
    text = await lta_client.call("get_stop_departures", stop="83139")
    assert "No buses currently serving stop 83139" in text


async def test_lta_limit_caps_services(lta_client, sg_api):
    sg_api.json(fa.LTA, sd.lta_response(
        *[sd.lta_service(str(n), sd.lta_bus(n)) for n in range(1, 11)]))
    text = await lta_client.call("get_stop_departures", stop="83139", limit=4)
    assert len(departure_lines(text)) == 4


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_lta_http_errors(lta_client, sg_api, status):
    sg_api.json(fa.LTA, {"fault": "nope"}, status=status)
    text = await lta_client.call("get_stop_departures", stop="83139")
    assert "LTA DataMall request failed for stop 83139" in text
    assert str(status) in text
    assert "test-key" not in text  # never leak the key


async def test_lta_key_not_used_for_stop_names(lta_client, sg_api):
    await lta_client.call("get_stop_departures", stop="Tampines")
    assert not sg_api.calls(fa.LTA)
    assert sg_api.calls(fa.STOPTIMES)
