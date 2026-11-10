"""Shared fixtures.

`client` is the REAL server.py running as a subprocess, driven over stdio
by the official MCP client - exactly how Claude Desktop talks to it - but
with every API URL pointed at a local FakeAPI, so no internet is needed.

For speed, one server process is shared by the whole run (its cache is
off, so tests can't affect each other); the fake API's routes and request
log are reset before every test. Tests needing different settings (an LTA
key, a cache file) launch their own server with `start_server(...)`.
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import fake_api as fa
import sample_data as sd

SERVER = Path(__file__).resolve().parent.parent / "server.py"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


# ---- fake API ---------------------------------------------------------------
@pytest.fixture(scope="session")
def _shared_api():
    fake = fa.FakeAPI().start()
    yield fake
    fake.stop()


@pytest.fixture
def api(_shared_api):
    """The fake API, emptied: every path returns 404 until a test adds it."""
    _shared_api.routes.clear()
    _shared_api.requests.clear()
    return _shared_api


@pytest.fixture
def sg_api(api):
    """The fake API pre-loaded with sensible Singapore answers.

    Tests override individual routes to inject failures and edge cases.
    """
    api.json(fa.GEOCODE, sd.geocode_response(sd.city()))
    api.json(fa.FORECAST, sd.forecast())
    # Each transit search returns a stop named after the search text.
    api.dynamic(fa.TRANSIT_GEOCODE,
                lambda q: [sd.transit_place(q["text"].title())])
    api.json(fa.PLAN, sd.plan(sd.fast_but_walky(), sd.slow_but_dry()))
    api.json(fa.STOPTIMES, sd.stoptimes(
        sd.stoptime(2, "15"), sd.stoptime(4, "150"), sd.stoptime(6, "13")))
    api.json(fa.LTA, sd.lta_response(sd.lta_service("15", sd.lta_bus(3))))
    return api


# ---- MCP server over stdio --------------------------------------------------
@pytest.fixture(scope="session")
def server_env(_shared_api, tmp_path_factory) -> dict:
    """Environment for the server subprocess: fake URLs, no cache, no key."""
    base = _shared_api.base_url
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path_factory.mktemp("home")),
        "OPEN_METEO_GEOCODING_URL": base + fa.GEOCODE,
        "OPEN_METEO_FORECAST_URL": base + fa.FORECAST,
        "TRANSITOUS_URL": base + "/transitous/api",
        "LTA_BUS_ARRIVAL_URL": base + fa.LTA,
        "LTA_ACCOUNT_KEY": "",
        "WEATHER_ADVISOR_CACHE": "off",
        "WEATHER_ADVISOR_TIMEOUT": "1",
    }


class Client:
    """Thin wrapper so tests read naturally: await client.call(...)."""

    def __init__(self, session: ClientSession):
        self.session = session

    async def call_raw(self, tool: str, **arguments):
        return await self.session.call_tool(tool, arguments)

    async def call(self, tool: str, **arguments) -> str:
        """Call a tool and return its text. Fails the test if the tool
        crashed (an unhandled exception inside the server)."""
        result = await self.call_raw(tool, **arguments)
        text = "\n".join(c.text for c in result.content if c.type == "text")
        assert not result.is_error, f"{tool} crashed: {text}"
        return text


@asynccontextmanager
async def launch(env: dict, log_path: Path):
    """Start server.py with `env` and yield a connected Client."""
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER)], env=env)
    with open(log_path, "a") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield Client(session)


@pytest.fixture(scope="session")
async def client(server_env, tmp_path_factory):
    """The shared server (cache off, no LTA key)."""
    log = tmp_path_factory.mktemp("logs") / "shared-server.log"
    async with launch(server_env, log) as c:
        yield c


@pytest.fixture
def start_server(server_env, tmp_path):
    """Factory for a dedicated server: `async with start_server(X="1") as c`."""

    def _start(**extra_env):
        return launch({**server_env, **extra_env}, tmp_path / "server.log")

    return _start
