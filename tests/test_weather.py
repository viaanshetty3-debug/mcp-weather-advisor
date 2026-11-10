"""geocode_location and get_current_weather over stdio."""

import pytest

import fake_api as fa
import sample_data as sd

pytestmark = pytest.mark.anyio


# ---- happy paths ------------------------------------------------------------
async def test_weather_report_contains_every_field(client, sg_api):
    sg_api.json(fa.FORECAST, sd.forecast(
        code=61, temp=27.5, feels=30.2, humidity=88, precip=1.4,
        wind=12.0, gusts=25.0))
    text = await client.call("get_current_weather", place="Singapore")
    assert "Weather in Singapore, Singapore" in text
    assert "Conditions: Light rain" in text
    assert "Temperature: 27.5°C (feels like 30.2°C)" in text
    assert "Humidity: 88%" in text
    assert "Precipitation: 1.4 mm" in text
    assert "Wind: 12.0 km/h (gusts 25.0 km/h)" in text
    assert "Served from cache" not in text  # cache is off in this server


async def test_weather_uses_coordinates_from_geocoder(client, sg_api):
    sg_api.json(fa.GEOCODE, sd.geocode_response(
        sd.city("Tokyo", lat=35.6895, lon=139.69171, country="Japan",
                tz="Asia/Tokyo")))
    await client.call("get_current_weather", place="Tokyo")
    query = sg_api.calls(fa.FORECAST)[0].query
    assert (query["latitude"], query["longitude"]) == ("35.6895", "139.69171")
    assert query["timezone"] == "auto"


async def test_geocode_location_output(client, sg_api):
    sg_api.json(fa.GEOCODE, sd.geocode_response(
        sd.city("Paris", 48.85341, 2.3488, "France", admin1="Île-de-France",
                tz="Europe/Paris")))
    text = await client.call("geocode_location", place="Paris")
    assert text.splitlines() == [
        "Paris, Île-de-France, France", "Latitude: 48.85341",
        "Longitude: 2.3488", "Timezone: Europe/Paris"]


async def test_geocode_missing_timezone_says_unknown(client, sg_api):
    place = sd.city()
    del place["timezone"]
    sg_api.json(fa.GEOCODE, sd.geocode_response(place))
    text = await client.call("geocode_location", place="Singapore")
    assert "Timezone: unknown" in text


async def test_unknown_weather_code_is_reported_as_unknown(client, sg_api):
    sg_api.json(fa.FORECAST, sd.forecast(code=42))
    text = await client.call("get_current_weather", place="Singapore")
    assert "Conditions: Unknown" in text


# ---- bad location queries ---------------------------------------------------
@pytest.mark.parametrize("tool", ["get_current_weather", "geocode_location"])
async def test_unknown_place(client, sg_api, tool):
    sg_api.json(fa.GEOCODE, sd.geocode_response())  # no "results" key
    text = await client.call(tool, place="Xyzzyqqq")
    assert "No location found for 'Xyzzyqqq'" in text
    assert not sg_api.calls(fa.FORECAST)


async def test_empty_results_list_is_unknown_place(client, sg_api):
    sg_api.json(fa.GEOCODE, {"results": []})
    text = await client.call("get_current_weather", place="Atlantis")
    assert "No location found" in text


@pytest.mark.parametrize("tool", ["get_current_weather", "geocode_location"])
@pytest.mark.parametrize("place", ["", "   ", "\n\t"])
async def test_blank_place_is_rejected_without_calling_api(
        client, sg_api, tool, place):
    text = await client.call(tool, place=place)
    assert "provide" in text.lower()
    assert not sg_api.requests


async def test_overlong_place_is_rejected_without_calling_api(client, sg_api):
    text = await client.call("get_current_weather", place="a" * 500)
    assert "too long" in text.lower()
    assert not sg_api.requests


async def test_query_is_normalised_before_lookup(client, sg_api):
    await client.call("get_current_weather", place="  NEW   york ")
    assert sg_api.calls(fa.GEOCODE)[0].query["name"] == "new york"


async def test_unicode_place_names_round_trip(client, sg_api):
    sg_api.json(fa.GEOCODE, sd.geocode_response(
        sd.city("São Paulo", -23.5475, -46.63611, "Brazil",
                tz="America/Sao_Paulo")))
    text = await client.call("get_current_weather", place="São Paulo")
    assert sg_api.calls(fa.GEOCODE)[0].query["name"] == "são paulo"
    assert "Weather in São Paulo, Brazil" in text


async def test_query_string_injection_is_encoded(client, sg_api):
    await client.call("geocode_location", place="Paris&count=50")
    query = sg_api.calls(fa.GEOCODE)[0].query
    assert query["name"] == "paris&count=50"
    assert query["count"] == "1"
