"""get_commute_advice over stdio."""

import re

import pytest

import fake_api as fa
import sample_data as sd

pytestmark = pytest.mark.anyio

ARGS = {"origin": "Orchard MRT", "destination": "Changi Airport"}


def recommended_block(text: str) -> str:
    """The lines between 'Recommended' and the next blank line."""
    return text.split("Recommended")[1].split("\n\n")[0]


# ---- happy paths ------------------------------------------------------------
async def test_good_weather_recommends_earliest_arrival(client, sg_api):
    text = await client.call("get_commute_advice", **ARGS)
    assert "Trip: Orchard Mrt -> Changi Airport" in text
    assert "Recommended (earliest arrival)" in text
    assert "Metro NS" in recommended_block(text)          # fast_but_walky
    assert "Other options:" in text and "Bus 7" in text  # slow_but_dry
    assert "No weather concerns." in text


@pytest.mark.parametrize("bad_weather", [
    sd.forecast(code=63),               # rain
    sd.forecast(code=0, precip=0.3),    # clear sky but it's raining
    sd.forecast(code=95),               # thunderstorm
    sd.forecast(feels=38),              # heat
    sd.forecast(wind=35),               # wind
], ids=["rain", "precip", "storm", "heat", "wind"])
async def test_bad_weather_recommends_least_walking(
        client, sg_api, bad_weather):
    sg_api.json(fa.FORECAST, bad_weather)
    text = await client.call("get_commute_advice", **ARGS)
    assert "Recommended (least walking, because of the weather)" in text
    assert "Bus 7" in recommended_block(text)


async def test_trip_times_are_shown_in_local_time(client, sg_api):
    trip = sd.slow_but_dry(start_min=30)
    sg_api.json(fa.PLAN, sd.plan(trip))
    text = await client.call("get_commute_advice", **ARGS)
    start, end = sd.local_hhmm(trip["startTime"]), sd.local_hhmm(trip["endTime"])
    assert f"{start} -> {end} (41 min, 1 transfer(s), 1 min walking)" in text


async def test_plan_request_uses_geocoded_coordinates(client, sg_api):
    sg_api.dynamic(fa.TRANSIT_GEOCODE, lambda q: [
        sd.transit_place("A", 1.1, 103.1) if "orchard" in q["text"]
        else sd.transit_place("B", 1.2, 103.2)])
    await client.call("get_commute_advice", **ARGS)
    query = sg_api.calls(fa.PLAN)[0].query
    assert query["fromPlace"] == "1.1,103.1"
    assert query["toPlace"] == "1.2,103.2"
    # Weather is checked at the START of the trip.
    assert sg_api.calls(fa.FORECAST)[0].query["latitude"] == "1.1"


async def test_at_most_three_options_are_shown(client, sg_api):
    sg_api.json(fa.PLAN, sd.plan(*[sd.slow_but_dry(m) for m in range(2, 8)]))
    text = await client.call("get_commute_advice", **ARGS)
    assert len(re.findall(r"\d\d:\d\d -> \d\d:\d\d \(", text)) == 3


async def test_live_and_scheduled_legs_are_labelled(client, sg_api):
    sg_api.json(fa.PLAN, sd.plan(sd.itinerary(2, [
        sd.ride(2, 5, route="S9", mode="SUBURBAN", realtime=True),
        sd.ride(8, 5, route="100", realtime=False)])))
    text = await client.call("get_commute_advice", **ARGS)
    assert "Train S9 towards Shenton Way" in text and "(live)" in text
    assert "Bus 100" in text and "(scheduled)" in text


async def test_zero_minute_walks_are_hidden(client, sg_api):
    sg_api.json(fa.PLAN, sd.plan(sd.itinerary(2, [
        sd.walk(2, 0, "Platform"), sd.ride(2, 10), sd.walk(12, 3, "END")])))
    text = await client.call("get_commute_advice", **ARGS)
    assert "Walk 0 min" not in text
    assert "Walk 3 min to END" in text


async def test_leg_without_route_or_headsign(client, sg_api):
    leg = sd.ride(2, 10, mode="FERRY")
    del leg["routeShortName"], leg["headsign"]
    sg_api.json(fa.PLAN, sd.plan(sd.itinerary(2, [leg])))
    text = await client.call("get_commute_advice", **ARGS)
    assert "- Ferry : Orchard Stn" in text and "towards" not in text


async def test_unknown_travel_mode_is_title_cased(client, sg_api):
    sg_api.json(fa.PLAN, sd.plan(sd.itinerary(2, [
        sd.ride(2, 10, mode="HOVERCRAFT")])))
    text = await client.call("get_commute_advice", **ARGS)
    assert "Hovercraft 106" in text


# ---- no-route / departed / timezone edge cases ------------------------------
async def test_no_route_found(client, sg_api):
    sg_api.json(fa.PLAN, {"itineraries": [], "direct": []})
    text = await client.call("get_commute_advice", **ARGS)
    assert "No public-transport route found" in text
    assert "Weather at start" in text  # weather is still reported


async def test_departed_trips_are_never_recommended(client, sg_api):
    sg_api.json(fa.PLAN, sd.plan(sd.fast_but_walky(start_min=-10),
                                 sd.slow_but_dry(start_min=5)))
    text = await client.call("get_commute_advice", **ARGS)
    assert "Metro NS" not in text
    assert "Bus 7" in recommended_block(text)


async def test_only_departed_trips_means_no_route(client, sg_api):
    sg_api.json(fa.PLAN, sd.plan(sd.slow_but_dry(start_min=-10)))
    text = await client.call("get_commute_advice", **ARGS)
    assert "No public-transport route found" in text


@pytest.mark.parametrize("tz", [None, "Mars/Olympus_Mons"])
async def test_missing_or_bad_timezone_does_not_crash(client, sg_api, tz):
    weather = sd.forecast()
    if tz is None:
        del weather["timezone"]
    else:
        weather["timezone"] = tz
    sg_api.json(fa.FORECAST, weather)
    text = await client.call("get_commute_advice", **ARGS)
    assert "Recommended" in text


# ---- bad location queries ---------------------------------------------------
async def test_unknown_origin(client, sg_api):
    sg_api.dynamic(fa.TRANSIT_GEOCODE, lambda q: [])
    text = await client.call("get_commute_advice",
                             origin="Qwzxqqq", destination="Changi Airport")
    assert "No place found for 'Qwzxqqq'" in text
    assert not sg_api.calls(fa.PLAN)


async def test_unknown_destination(client, sg_api):
    sg_api.dynamic(fa.TRANSIT_GEOCODE, lambda q: (
        [] if "nowhere" in q["text"] else [sd.transit_place("Orchard")]))
    text = await client.call("get_commute_advice",
                             origin="Orchard", destination="Nowhere Land")
    assert "No place found for 'Nowhere Land'" in text
    assert not sg_api.calls(fa.PLAN)


@pytest.mark.parametrize("origin, destination", [
    ("", "Changi Airport"), ("Orchard", "   "), ("", "")])
async def test_blank_origin_or_destination_rejected(
        client, sg_api, origin, destination):
    text = await client.call("get_commute_advice",
                             origin=origin, destination=destination)
    assert "provide" in text.lower()
    assert not sg_api.requests


async def test_landmarks_are_accepted_as_endpoints(client, sg_api):
    sg_api.dynamic(fa.TRANSIT_GEOCODE, lambda q: [
        sd.transit_place("Marina Bay Sands", type_="PLACE")])
    text = await client.call("get_commute_advice",
                             origin="Marina Bay Sands", destination="MBS")
    assert "Trip: Marina Bay Sands -> Marina Bay Sands" in text
