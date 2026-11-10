"""MCP protocol-level behaviour over stdio: tool discovery and bad calls."""

import pytest

import fake_api as fa

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {
    "geocode_location": {"place"},
    "get_current_weather": {"place"},
    "get_commute_advice": {"origin", "destination"},
    "get_stop_departures": {"stop"},
}


async def test_lists_exactly_the_expected_tools(client, api):
    tools = (await client.session.list_tools()).tools
    assert {t.name for t in tools} == set(EXPECTED_TOOLS)


@pytest.mark.parametrize("name, required", EXPECTED_TOOLS.items())
async def test_tool_schema_and_description(client, api, name, required):
    tool = next(t for t in (await client.session.list_tools()).tools
                if t.name == name)
    assert set(tool.input_schema.get("required", [])) == required
    # Claude chooses tools from their descriptions, so they must exist.
    assert tool.description and len(tool.description) > 30


async def test_departures_limit_is_optional_integer(client, api):
    tool = next(t for t in (await client.session.list_tools()).tools
                if t.name == "get_stop_departures")
    limit = tool.input_schema["properties"]["limit"]
    assert limit["type"] == "integer" and limit["default"] == 8


async def _assert_rejected(client, tool, **args):
    """A bad call must come back as an error, not hang or kill the server."""
    try:
        result = await client.call_raw(tool, **args)
    except Exception:
        return  # raised as a protocol error: also acceptable
    assert result.is_error


async def test_unknown_tool_is_rejected(client, api):
    await _assert_rejected(client, "get_moon_phase", place="Singapore")


async def test_missing_required_argument_is_rejected(client, api):
    await _assert_rejected(client, "get_commute_advice", origin="Orchard")


async def test_wrong_argument_type_is_rejected(client, api):
    await _assert_rejected(client, "get_stop_departures",
                           stop="83139", limit="lots")


async def test_server_survives_bad_calls(client, sg_api):
    await _assert_rejected(client, "get_moon_phase")
    await _assert_rejected(client, "get_current_weather")
    text = await client.call("get_current_weather", place="Singapore")
    assert "Weather in Singapore" in text
    assert len(sg_api.calls(fa.FORECAST)) == 1
