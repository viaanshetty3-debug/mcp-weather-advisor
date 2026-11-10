"""Structured logging (mcp_server.log) and stdio-stream safety.

Each failure type must:
  1. reach the user as a readable message (never a crash),
  2. be written to the log as ONE valid JSON line with useful fields,
  3. leave stdout - the MCP protocol channel - untouched.
"""

import json
import logging
import os
import select
import subprocess
import sys
import time

import pytest
from mcp.types import LATEST_PROTOCOL_VERSION

import fake_api as fa
import sample_data as sd
import server
from conftest import SERVER, launch

pytestmark = pytest.mark.anyio


# ---- helpers ----------------------------------------------------------------
class LogReader:
    """Reads only the log lines written since the last call."""

    def __init__(self, path):
        self.path, self.offset = path, 0

    def new(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            f.seek(self.offset)
            text = f.read()
            self.offset = f.tell()
        # Every line must be valid JSON - that's the point of the format.
        return [json.loads(line) for line in text.splitlines() if line]

    def errors(self) -> list[dict]:
        return [r for r in self.new() if r["level"] in ("WARNING", "ERROR",
                                                        "CRITICAL")]


@pytest.fixture(scope="module")
def log_path(tmp_path_factory):
    return tmp_path_factory.mktemp("logtest") / "mcp_server.log"


@pytest.fixture(scope="module")
async def log_client(server_env, log_path):
    env = {**server_env, "WEATHER_ADVISOR_LOG": str(log_path)}
    async with launch(env, log_path.with_suffix(".stderr")) as client:
        yield client


@pytest.fixture
def logs(log_client, log_path):
    reader = LogReader(log_path)
    reader.new()  # skip anything earlier tests wrote
    return reader


def only(records, event):
    matching = [r for r in records if r["event"] == event]
    assert len(matching) == 1, f"expected one {event!r}, got {records}"
    return matching[0]


# ---- record format ----------------------------------------------------------
async def test_server_start_is_logged(log_client, log_path):
    first = json.loads(log_path.read_text().splitlines()[0])
    assert first["event"] == "server_start" and first["level"] == "INFO"
    assert first["lta_key_configured"] is False


async def test_success_writes_no_error_records(log_client, sg_api, logs):
    await log_client.call("get_current_weather", place="Singapore")
    await log_client.call("get_commute_advice", origin="A", destination="B")
    await log_client.call("get_stop_departures", stop="83139")
    assert logs.errors() == []


async def test_records_are_tagged_with_tool_and_call(log_client, sg_api, logs):
    sg_api.json(fa.FORECAST, {}, status=500)
    await log_client.call("get_current_weather", place="Singapore")
    await log_client.call("get_current_weather", place="Singapore")
    first, second = logs.errors()
    for record in (first, second):
        assert record["tool"] == "get_current_weather"
        assert len(record["call_id"]) == 8
        assert record["ts"].endswith("Z")
    assert first["call_id"] != second["call_id"]


# ---- network failures -------------------------------------------------------
async def test_timeout(log_client, sg_api, logs):
    sg_api.json(fa.FORECAST, {}, delay=1.5)
    text = await log_client.call("get_current_weather", place="Singapore")
    assert "did not respond within 1 seconds" in text
    record = only(logs.errors(), "api_timeout")
    assert record["level"] == "ERROR"
    assert record["url"].endswith(fa.FORECAST)
    assert record["timeout_s"] == 1.0
    assert record["elapsed_ms"] >= 900
    assert record["error"] == "ReadTimeout"


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_http_error(log_client, sg_api, logs, status):
    sg_api.raw(fa.PLAN, b"<html>upstream down</html>", status=status)
    text = await log_client.call("get_commute_advice",
                                 origin="A", destination="B")
    assert f"HTTP {status}" in text
    record = only(logs.errors(), "api_http_error")
    assert record["status"] == status
    assert record["body_snippet"] == "<html>upstream down</html>"
    assert record["params"]["numItineraries"] == 6


@pytest.mark.parametrize("body", [b"<html>oops</html>", b"", b"{truncated"])
async def test_invalid_json(log_client, sg_api, logs, body):
    sg_api.raw(fa.STOPTIMES, body, status=200)
    text = await log_client.call("get_stop_departures", stop="83139")
    assert "isn't valid JSON" in text
    record = only(logs.errors(), "api_invalid_json")
    assert record["status"] == 200
    assert record["body_snippet"] == body.decode()


async def test_invalid_json_snippet_is_capped(log_client, sg_api, logs):
    sg_api.raw(fa.FORECAST, b"x" * 5000, status=200)
    await log_client.call("get_current_weather", place="Singapore")
    record = only(logs.errors(), "api_invalid_json")
    assert len(record["body_snippet"]) == 200


async def test_unreachable(server_env, tmp_path):
    log = tmp_path / "mcp_server.log"
    env = {**server_env, "WEATHER_ADVISOR_LOG": str(log),
           "OPEN_METEO_GEOCODING_URL": "http://127.0.0.1:9/geo"}
    async with launch(env, tmp_path / "stderr") as client:
        text = await client.call("get_current_weather", place="Singapore")
    assert "could not reach the service (ConnectError)" in text
    record = only(LogReader(log).errors(), "api_unreachable")
    assert record["error"] == "ConnectError"
    assert record["url"] == "http://127.0.0.1:9/geo"


# ---- coordinate lookup failures ---------------------------------------------
async def test_city_not_found(log_client, sg_api, logs):
    sg_api.json(fa.GEOCODE, sd.geocode_response())
    text = await log_client.call("get_current_weather", place="Atlantis")
    assert "No location found for 'Atlantis'" in text
    record = only(logs.errors(), "location_not_found")
    assert record["level"] == "WARNING"
    assert (record["service"], record["query"]) == ("open-meteo", "Atlantis")


async def test_transit_place_not_found(log_client, sg_api, logs):
    sg_api.json(fa.TRANSIT_GEOCODE, [])
    await log_client.call("get_commute_advice",
                          origin="Nowhere", destination="B")
    record = only(logs.errors(), "location_not_found")
    assert record["service"] == "transitous"
    assert record["wanted"] == "place"


async def test_no_stop_found(log_client, sg_api, logs):
    sg_api.json(fa.TRANSIT_GEOCODE, [sd.transit_place("Mall", type_="PLACE")])
    await log_client.call("get_stop_departures", stop="Mall")
    assert only(logs.errors(), "location_not_found")["wanted"] == "transit stop"


@pytest.mark.parametrize("lat, lon", [
    (None, 103.8), ("1.3", 103.8), (True, 103.8), (95.0, 103.8),
    (1.3, -181), (float("nan"), 0)],
    ids=["missing", "string", "bool", "lat>90", "lon<-180", "nan"])
async def test_bad_city_coordinates(log_client, sg_api, logs, lat, lon):
    place = sd.city()
    place["latitude"], place["longitude"] = lat, lon
    if lat is None:
        del place["latitude"]
    # json.dumps writes NaN as a bare `NaN`, as some broken APIs do.
    sg_api.json(fa.GEOCODE, sd.geocode_response(place))
    text = await log_client.call("get_current_weather", place="Singapore")
    assert "invalid coordinates for 'Singapore'" in text
    record = only(logs.errors(), "bad_coordinates")
    assert record["service"] == "open-meteo"
    assert not sg_api.calls(fa.FORECAST)  # never used bad coordinates


async def test_bad_transit_coordinates(log_client, sg_api, logs):
    sg_api.json(fa.TRANSIT_GEOCODE,
                [{"type": "STOP", "name": "X", "lat": 400, "lon": 0}])
    text = await log_client.call("get_commute_advice",
                                 origin="X", destination="Y")
    assert "invalid coordinates" in text
    record = only(logs.errors(), "bad_coordinates")
    assert (record["service"], record["latitude"]) == ("transitous", 400)
    assert not sg_api.calls(fa.PLAN)


# ---- other failures ---------------------------------------------------------
async def test_unexpected_shape_logs_traceback(log_client, sg_api, logs):
    sg_api.json(fa.FORECAST, {"current": {"time": "now"}})
    text = await log_client.call("get_current_weather", place="Singapore")
    assert "unexpected format" in text
    record = only(logs.errors(), "api_bad_shape")
    assert record["error"] == "KeyError"
    assert "Traceback" in record["traceback"]
    assert "get_current_weather" in record["traceback"]
    assert record["arguments"] == {"place": "Singapore"}


async def test_invalid_input_is_logged(log_client, sg_api, logs):
    await log_client.call("get_current_weather", place="   ")
    record = only(logs.errors(), "invalid_input")
    assert record["field"] == "a place name"
    assert not sg_api.requests


async def test_overlong_argument_is_truncated_in_log(log_client, sg_api, logs):
    sg_api.json(fa.FORECAST, {"current": {}})   # force an api_bad_shape
    sg_api.json(fa.GEOCODE, sd.geocode_response(sd.city()))
    await log_client.call("get_current_weather", place="S" * 150)
    record = only(logs.errors(), "api_bad_shape")
    assert record["arguments"]["place"] == "S" * 60 + "..."


async def test_each_failure_is_logged_once(log_client, sg_api, logs):
    """Request-level and tool-level logging must not double up."""
    sg_api.json(fa.FORECAST, {}, status=500)
    await log_client.call("get_current_weather", place="Singapore")
    assert len(logs.errors()) == 1


# ---- secrets ----------------------------------------------------------------
async def test_api_key_never_logged(server_env, tmp_path, sg_api):
    log = tmp_path / "mcp_server.log"
    env = {**server_env, "WEATHER_ADVISOR_LOG": str(log),
           "LTA_ACCOUNT_KEY": "super-secret-key"}
    sg_api.json(fa.LTA, {"fault": "invalid key"}, status=401)
    async with launch(env, tmp_path / "stderr") as client:
        text = await client.call("get_stop_departures", stop="83139")
    assert "LTA DataMall request failed for stop 83139: " in text
    assert only(LogReader(log).errors(), "api_http_error")["status"] == 401
    assert "super-secret-key" not in log.read_text()
    assert only(LogReader(log).new(), "server_start")["lta_key_configured"]


def test_secret_looking_params_are_redacted():
    safe = server._safe_params({"AccountKey": "k", "api_token": "t",
                                "stopId": "s"})
    assert safe == {"AccountKey": "[redacted]", "api_token": "[redacted]",
                    "stopId": "s"}


# ---- log file handling ------------------------------------------------------
async def test_log_disabled(server_env, tmp_path, sg_api):
    env = {**server_env, "WEATHER_ADVISOR_LOG": "off"}
    sg_api.json(fa.FORECAST, {}, status=500)
    async with launch(env, tmp_path / "stderr") as client:
        text = await client.call("get_current_weather", place="Singapore")
    assert "HTTP 500" in text
    assert not list(tmp_path.glob("*.log"))


async def test_unwritable_log_falls_back_to_stderr(server_env, tmp_path,
                                                   sg_api):
    stderr = tmp_path / "stderr"
    env = {**server_env,
           "WEATHER_ADVISOR_LOG": str(tmp_path / "no-such-dir" / "x.log")}
    sg_api.json(fa.FORECAST, {}, status=500)
    async with launch(env, stderr) as client:
        text = await client.call("get_current_weather", place="Singapore")
    assert "HTTP 500" in text
    events = [json.loads(line)["event"] for line in
              stderr.read_text().splitlines() if line.startswith("{")]
    assert "log_file_unavailable" in events and "api_http_error" in events


@pytest.fixture
def restore_logging():
    yield
    server._setup_logging("off")


def test_log_rotation(tmp_path, restore_logging):
    path = tmp_path / "rotating.log"
    server._setup_logging(str(path), max_bytes=2000, backups=2)
    for i in range(100):
        server._log_event(logging.ERROR, "filler", n=i, pad="x" * 50)
    assert path.exists()
    assert (tmp_path / "rotating.log.1").exists()
    assert (tmp_path / "rotating.log.2").exists()
    assert not (tmp_path / "rotating.log.3").exists()   # only 2 backups
    for name in ("rotating.log", "rotating.log.1"):
        for line in (tmp_path / name).read_text().splitlines():
            json.loads(line)  # rotation never splits a record


def test_non_json_values_are_logged_as_text(tmp_path, restore_logging):
    path = tmp_path / "x.log"
    server._setup_logging(str(path))
    server._log_event(logging.ERROR, "odd", value=object(), when=time)
    record = json.loads(path.read_text())
    assert record["value"].startswith("<object object")


async def test_unexpected_exception_is_contained(tmp_path, restore_logging):
    """Any exception type - not just expected ones - becomes a message."""
    path = tmp_path / "x.log"
    server._setup_logging(str(path))

    @server.friendly_errors("Could not do '{thing}'")
    async def broken_tool(thing: str) -> str:
        raise RuntimeError("something nobody anticipated")

    text = await broken_tool(thing="it")
    assert text == ("Could not do 'it': an unexpected internal error "
                    "occurred (details in the log).")
    record = json.loads(path.read_text())
    assert record["event"] == "tool_crashed"
    assert record["tool"] == "broken_tool"
    assert "RuntimeError: something nobody anticipated" in record["traceback"]


async def test_call_context_is_cleared_after_a_tool(tmp_path, restore_logging):
    path = tmp_path / "x.log"
    server._setup_logging(str(path))

    @server.friendly_errors("x")
    async def ok_tool() -> str:
        return "fine"

    await ok_tool()
    server._log_event(logging.ERROR, "outside")
    assert "tool" not in json.loads(path.read_text())


# ---- the stdio stream itself ------------------------------------------------
def _rpc(proc, message: dict, timeout: float = 10) -> dict | None:
    """Send one JSON-RPC message; if it has an id, return its response.

    Every stdout line must be a valid JSON-RPC message - a single stray
    print() or log line on stdout would fail this.
    """
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if "id" not in message:
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.2)
        if not ready:
            continue
        line = proc.stdout.readline()
        assert line, "server closed stdout"
        reply = json.loads(line)               # must be JSON ...
        assert reply.get("jsonrpc") == "2.0"   # ... and JSON-RPC
        if reply.get("id") == message["id"]:
            return reply
    raise AssertionError(f"no reply to {message['method']}")


def test_stdout_carries_only_protocol_messages(server_env, sg_api, tmp_path):
    sg_api.json(fa.FORECAST, {}, delay=1.5)                      # timeout
    sg_api.raw(fa.STOPTIMES, b"<html>not json</html>")           # bad JSON
    sg_api.json(fa.TRANSIT_GEOCODE, [{"type": "STOP", "name": "X",
                                      "lat": 999, "lon": 0}])    # bad coords
    log = tmp_path / "mcp_server.log"
    env = {**server_env, "WEATHER_ADVISOR_LOG": str(log)}
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)], env=env, text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=open(tmp_path / "stderr", "w"))
    try:
        init = _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": LATEST_PROTOCOL_VERSION,
                                      "capabilities": {},
                                      "clientInfo": {"name": "t",
                                                     "version": "0"}}})
        assert "result" in init
        _rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        calls = [("get_current_weather", {"place": "Singapore"}),
                 ("get_stop_departures", {"stop": "83139"}),
                 ("get_commute_advice", {"origin": "X", "destination": "Y"}),
                 ("get_current_weather", {"place": ""})]
        for n, (tool, args) in enumerate(calls, start=2):
            reply = _rpc(proc, {"jsonrpc": "2.0", "id": n,
                                "method": "tools/call",
                                "params": {"name": tool, "arguments": args}})
            result = reply["result"]
            assert not result.get("isError"), result
            assert result["content"][0]["text"].startswith("Could not")
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)

    events = {json.loads(line)["event"] for line in log.read_text().splitlines()}
    assert {"api_timeout", "api_invalid_json", "bad_coordinates",
            "invalid_input", "server_start"} <= events
    assert os.path.getsize(log) > 0
