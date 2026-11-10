"""Fast in-process unit tests for server.py's pure helper functions."""

import sqlite3
import time

import pytest

import sample_data as sd
from mcp_weather_advisor import server


# ---- weather assessment -----------------------------------------------------
def now(code=0, feels=20.0, wind=5.0, gusts=10.0, precip=0.0):
    return sd.forecast(code=code, feels=feels, wind=wind, gusts=gusts,
                       precip=precip)["current"]


@pytest.mark.parametrize("conditions, walking, cycling, warning", [
    (now(), "Good", "Good", None),
    (now(code=63), "Fair", "Fair", "Rain"),
    (now(code=0, precip=0.1), "Fair", "Fair", "Rain"),
    (now(code=95), "Poor", "Poor", "Thunderstorm"),
    (now(code=73), "Poor", "Poor", "Icy, snowy or foggy"),
    (now(code=45), "Fair", "Fair", "Icy, snowy or foggy"),
    (now(code=66), "Fair", "Fair", "Icy, snowy or foggy"),
    (now(gusts=50), "Poor", "Poor", "Strong gusts"),
    (now(gusts=49.9), "Good", "Good", None),
    (now(wind=30), "Fair", "Poor", "Windy"),
    (now(wind=29.9), "Good", "Good", None),
    (now(feels=0), "Fair", "Fair", "Freezing"),
    (now(feels=0.1), "Good", "Good", None),
    (now(feels=35), "Fair", "Fair", "Very hot"),
    (now(feels=34.9), "Good", "Good", None),
], ids=["calm", "rain", "precip-only", "storm", "snow", "fog", "freezing-rain",
        "gusts-50", "gusts-49.9", "wind-30", "wind-29.9", "feels-0",
        "feels-0.1", "feels-35", "feels-34.9"])
def test_assess_weather_thresholds(conditions, walking, cycling, warning):
    result = server._assess_weather(conditions)
    assert (result["walking"], result["cycling"]) == (walking, cycling)
    assert result["bad_weather"] == (warning is not None)
    if warning:
        assert any(w.startswith(warning) for w in result["warnings"])
    else:
        assert result["warnings"] == []


def test_assess_weather_collects_multiple_warnings():
    result = server._assess_weather(now(code=95, gusts=60, feels=-3))
    assert len(result["warnings"]) == 3


def test_strong_gusts_suppress_separate_wind_warning():
    result = server._assess_weather(now(wind=40, gusts=70))
    assert [w.split()[0] for w in result["warnings"]] == ["Strong"]


# ---- time helpers -----------------------------------------------------------
@pytest.mark.parametrize("iso, tz, expected", [
    ("2026-11-10T03:02:00Z", "Asia/Singapore", "11:02"),
    ("2026-11-10T03:02:00Z", "Europe/Berlin", "04:02"),
    ("2026-11-10T23:30:00Z", "Asia/Tokyo", "08:30"),       # next day
    ("2026-11-10T03:02:00Z", None, "03:02"),
    ("2026-11-10T03:02:00Z", "Not/AZone", "03:02"),
    ("2026-11-10T11:02:00+08:00", "Asia/Singapore", "11:02"),
])
def test_local_time(iso, tz, expected):
    assert server._local_time(iso, tz) == expected


def test_is_future():
    assert server._is_future(sd.iso_utc(1))
    assert not server._is_future(sd.iso_utc(-1))
    assert server._is_future(sd.iso_sg(1))   # offset format (LTA)


# ---- text helpers -----------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("Singapore", "singapore"), ("  New   York ", "new york"),
    ("SÃO\tPAULO", "são paulo"), ("", "")])
def test_normalise(raw, expected):
    assert server._normalise(raw) == expected


@pytest.mark.parametrize("age, expected", [
    (0, ""), (-1, ""), (5, "<1 min"), (59.9, "<1 min"), (60, "1 min"),
    (899, "14 min")])
def test_freshness(age, expected):
    text = server._freshness(age)
    if expected:
        assert f"fetched {expected} ago" in text
    else:
        assert text == ""


def test_walk_minutes_counts_only_walking():
    trip = sd.fast_but_walky()
    assert server._walk_minutes(trip) == 15
    assert server._walk_minutes(sd.itinerary(0, [sd.ride(0, 30)])) == 0


def test_describe_itinerary_skips_zero_minute_walks():
    trip = sd.itinerary(5, [sd.walk(5, 0, "X"), sd.ride(5, 10)])
    lines = server._describe_itinerary(trip, "Asia/Singapore")
    assert len(lines) == 2 and "Walk" not in "".join(lines)


# ---- LTA formatting ---------------------------------------------------------
def test_format_lta_full():
    data = sd.lta_response(
        sd.lta_service("15", sd.lta_bus(0.2), sd.lta_bus(7.5, "SDA", 0)),
        sd.lta_service("150", {"EstimatedArrival": ""}))
    lines = server._format_lta_arrivals("83139", data, 8).splitlines()
    assert lines[1] == ("- Bus 15: Arr (seats available), 7 min "
                        "(standing available) [scheduled]")
    assert lines[2] == "- Bus 150: no estimate"


def test_format_lta_empty():
    assert "No buses" in server._format_lta_arrivals("1", {"Services": []}, 8)
    assert "No buses" in server._format_lta_arrivals("1", {}, 8)


# ---- cache primitives -------------------------------------------------------
@pytest.fixture
def temp_cache(tmp_path, monkeypatch):
    path = tmp_path / "unit-cache.sqlite3"
    monkeypatch.setattr(server, "CACHE_PATH", str(path))
    monkeypatch.setattr(server, "_cache_db", None)
    yield path
    if server._cache_db is not None:
        server._cache_db.close()


def test_cache_round_trip(temp_cache):
    key = server._cache_key("https://x/y", {"b": 2, "a": 1})
    server._cache_write(key, {"hello": ["world"]}, ttl=60)
    data, age = server._cache_read(key)
    assert data == {"hello": ["world"]} and 0 <= age < 1


def test_cache_key_ignores_param_order():
    assert (server._cache_key("u", {"a": 1, "b": 2})
            == server._cache_key("u", {"b": 2, "a": 1}))


def test_cache_entry_expires(temp_cache):
    server._cache_write("k", [1], ttl=60)
    with sqlite3.connect(temp_cache) as db:
        db.execute("UPDATE responses SET expires_at = ?", (time.time() - 1,))
    assert server._cache_read("k") is None


def test_writing_purges_expired_rows(temp_cache):
    server._cache_write("old", [1], ttl=60)
    with sqlite3.connect(temp_cache) as db:
        db.execute("UPDATE responses SET expires_at = 0")
    server._cache_write("new", [2], ttl=60)
    with sqlite3.connect(temp_cache) as db:
        keys = [r[0] for r in db.execute("SELECT key FROM responses")]
    assert keys == ["new"]


def test_cache_off(monkeypatch):
    monkeypatch.setattr(server, "CACHE_PATH", "OFF")
    monkeypatch.setattr(server, "_cache_db", None)
    server._cache_write("k", [1], ttl=60)
    assert server._cache_read("k") is None
