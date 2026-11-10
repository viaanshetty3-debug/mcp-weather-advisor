# MCP Weather & Transit Advisor

[![Tests](https://github.com/<your-username>/mcp-weather-advisor/actions/workflows/tests.yml/badge.svg)](https://github.com/<your-username>/mcp-weather-advisor/actions/workflows/tests.yml)

A small [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude check real-time weather and plan real public-transport trips, then recommend a route that suits the weather.

## Tools

| Tool | What it does |
|------|--------------|
| `geocode_location` | Converts a city name into latitude, longitude and timezone. |
| `get_current_weather` | Current conditions: temperature, feels-like, humidity, precipitation, wind. |
| `get_commute_advice` | Plans public-transport trips between two places (stations, landmarks, addresses) using real timetables. In rain, heat, wind or snow it recommends the option with the least walking; otherwise the earliest arrival. |
| `get_stop_departures` | Upcoming buses/trains at a stop, by name or Singapore 5-digit bus stop code. |

## Data sources

| Source | Used for | Key needed? |
|--------|----------|-------------|
| [Open-Meteo](https://open-meteo.com) | Weather, city geocoding | No |
| [Transitous](https://transitous.org) | Trip planning, stop timetables, stop/landmark search. Community-run router over open GTFS feeds; shows live delays where operators publish them | No |
| [LTA DataMall](https://datamall.lta.gov.sg) | Live Singapore bus arrivals with crowding levels | Yes (free) |

Transit coverage depends on which operators publish open data. Singapore, most of Europe and much of North America are covered; some regions are not. Times marked `scheduled` are timetabled, `live` include real-time updates.

### Optional: live Singapore bus arrivals
1. Request a free AccountKey at <https://datamall.lta.gov.sg/content/datamall/en/request-for-api.html>.
2. Provide it as the `LTA_ACCOUNT_KEY` environment variable (see the Claude Desktop config below).

Without a key, `get_stop_departures` falls back to Transitous timetables for Singapore stop codes.

## Caching

API answers are cached in a local SQLite file (`~/.mcp-weather-advisor/cache.sqlite3`) so repeated questions are answered instantly and the free APIs aren't hammered. Cached answers end with *"(Served from cache, fetched N min ago.)"*.

| Data | Kept for | Notes |
|------|----------|-------|
| Weather | 15 min | |
| Trip plans and stop timetables | 15 min | Trips and departures that have already left are dropped before answering; if none are left, the API is asked again. |
| Live LTA bus arrivals | 1 min | Live ETAs go stale quickly. Minutes-to-arrival are recomputed from the stored arrival time. |
| Place lookups (geocoding) | 7 days | Coordinates don't change. |

- Change the TTLs at the top of `mcp_weather_advisor/server.py` (`WEATHER_TTL`, `TRANSIT_TTL`, ...).
- `WEATHER_ADVISOR_CACHE=/path/to/file.sqlite3` stores the cache elsewhere; `WEATHER_ADVISOR_CACHE=off` disables it.
- API keys are never written to the cache. If the cache file can't be opened, the server simply calls the APIs directly.
- To clear the cache, delete `~/.mcp-weather-advisor/cache.sqlite3`.

## Installation

### 1. Install Python 3.10 or newer
- **macOS:** download from <https://www.python.org/downloads/> (or `brew install python`).
- **Windows:** download from the same page and tick **"Add python.exe to PATH"** in the installer.
- Check it worked: `python3 --version` (on Windows: `python --version`).

### 2. Clone the project and create a virtual environment
A virtual environment (`.venv`) is a private Python install for this project. Create it **inside the project folder**: the Claude Desktop config below points at it.

**macOS / Linux**
```bash
git clone https://github.com/<your-username>/mcp-weather-advisor.git
cd mcp-weather-advisor
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

**Windows (PowerShell)**
```powershell
git clone https://github.com/<your-username>/mcp-weather-advisor.git
cd mcp-weather-advisor
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

`pip install -e .` reads `pyproject.toml` and:
- installs the dependencies (`mcp`, `httpx`);
- installs the server as an *editable* package, so edits to the code take effect without reinstalling;
- adds a **`mcp-weather-advisor`** command that starts the server.

> **Installing without a virtual environment:** `pip install -e .` also works system-wide, but some Pythons (Homebrew's, and many Linux distributions') refuse with an `externally-managed-environment` error. There, use the virtual environment above, or [pipx](https://pipx.pypa.io): `pipx install -e .`.

> **Intel Mac note:** `cryptography` (a dependency of `mcp`) stopped publishing Intel Mac wheels after version 48. `pyproject.toml` therefore keeps it below 49 on Intel Macs only, so the install doesn't try to compile it from source. Other platforms get the latest version.

### 3. Check it runs
```bash
python server.py
```
The server starts and waits silently for Claude to talk to it over stdio. That's expected; press `Ctrl+C` to stop. `mcp-weather-advisor` and `python -m mcp_weather_advisor` start the same server.

To try the tools interactively in the MCP Inspector (needs Node.js and `pip install -e ".[dev]"`):
```bash
mcp dev mcp_weather_advisor/server.py
```

## Connect to Claude Desktop

Claude Desktop starts the server itself, using two **absolute paths** from its config file:

| Config key | Points to | Example (macOS) |
|------------|-----------|-----------------|
| `command` | the Python **inside your `.venv`** | `/Users/alex/mcp-weather-advisor/.venv/bin/python` |
| `args` | the `server.py` in the project's top folder | `/Users/alex/mcp-weather-advisor/server.py` |

### Step 1: Get your exact block (recommended)
With the virtual environment activated, run this **from the project folder** (macOS/Linux). It prints the block with your real paths filled in:

```bash
python -c 'import json, os, sys; print(json.dumps({"mcpServers": {"weather-transit-advisor": {"command": sys.executable, "args": [os.path.abspath("server.py")]}}}, indent=2))'
```

Or write it by hand. Find the project folder's absolute path with `pwd` (macOS/Linux) or `(Get-Location).Path` (Windows), then fill in one of the templates below.

### Step 2: The configuration block

**macOS / Linux** (replace `/Users/alex/mcp-weather-advisor` with your project folder):
```json
{
  "mcpServers": {
    "weather-transit-advisor": {
      "command": "/Users/alex/mcp-weather-advisor/.venv/bin/python",
      "args": [
        "/Users/alex/mcp-weather-advisor/server.py"
      ]
    }
  }
}
```

**Windows** (replace `C:\\Users\\alex\\mcp-weather-advisor`, keeping the **double backslashes** that JSON requires):
```json
{
  "mcpServers": {
    "weather-transit-advisor": {
      "command": "C:\\Users\\alex\\mcp-weather-advisor\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\alex\\mcp-weather-advisor\\server.py"
      ]
    }
  }
}
```

**Optional: live Singapore bus arrivals.** If you have an [LTA DataMall key](#optional-live-singapore-bus-arrivals), add an `env` block after `args`. Remember the comma after the closing `]`:
```json
{
  "mcpServers": {
    "weather-transit-advisor": {
      "command": "/Users/alex/mcp-weather-advisor/.venv/bin/python",
      "args": [
        "/Users/alex/mcp-weather-advisor/server.py"
      ],
      "env": {
        "LTA_ACCOUNT_KEY": "paste-your-real-key-here"
      }
    }
  }
}
```
Leave `env` out entirely if you don't have a key. A placeholder value causes 401 errors for Singapore stop codes.

### Step 3: Paste it into Claude Desktop
1. In Claude Desktop, open **Settings → Developer → Edit Config**. This opens `claude_desktop_config.json`:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
2. Paste the block in:
   - **If the file is empty or `{}`:** replace its contents with the block.
   - **If it already has an `"mcpServers"` section:** don't paste a second one. Copy only the `"weather-transit-advisor": { ... }` entry into the existing `"mcpServers"` object, with a comma between it and the server before it:
     ```json
     {
       "mcpServers": {
         "some-other-server": { "command": "..." },
         "weather-transit-advisor": {
           "command": "/Users/alex/mcp-weather-advisor/.venv/bin/python",
           "args": ["/Users/alex/mcp-weather-advisor/server.py"]
         }
       }
     }
     ```
3. Save, then **fully quit Claude Desktop** (macOS: ⌘Q; Windows: right-click the tray icon → Quit) and reopen it. Just closing the window isn't enough.
4. The tools appear under the tools (🔨) menu in a new chat. Try:
   - *"What's the weather in Lisbon, and should I cycle to work?"*
   - *"How do I get from Orchard MRT to Changi Airport right now?"*
   - *"When's the next bus at stop 83139?"*

### Common mistakes

| Symptom | Cause | Fix |
|---------|-------|-----|
| Server fails with `No module named 'httpx'` (or `'mcp'`) | `command` is a Python *outside* the `.venv`: the system `python3`, or the real file the `.venv` Python links to | Use the path ending in `.venv/bin/python` (Windows: `.venv\Scripts\python.exe`) **as-is**. Don't resolve it with `readlink`/`realpath`. |
| Server fails to start / file not found | Relative paths like `.venv/bin/python` or `server.py`. Claude Desktop doesn't start in your project folder. | Use full absolute paths starting with `/` (or `C:\\` on Windows). |
| Claude Desktop reports invalid JSON | Single backslashes on Windows, a missing comma between servers, or a trailing comma after the last item | Use `\\` in Windows paths, and check commas. Paste the file into a JSON validator if unsure. |
| No tools appear | Claude Desktop wasn't fully restarted | Quit it completely (⌘Q / tray → Quit) and reopen. |
| You moved or renamed the project folder | The paths in the config are now wrong | Re-run the Step 1 command and update the config. |

Claude Desktop's own log for this server is `~/Library/Logs/Claude/mcp-server-weather-transit-advisor.log` on macOS (Windows: `%APPDATA%\Claude\logs\`). It shows startup errors such as the ones above. Problems *inside* the tools go to this project's own log; see [Logging](#logging).

> **Alternative:** instead of `python` + `server.py`, `command` can be the installed launcher alone (`/Users/alex/mcp-weather-advisor/.venv/bin/mcp-weather-advisor`, Windows `...\\.venv\\Scripts\\mcp-weather-advisor.exe`) with no `args`. Both start the same server.

## Project layout

```
mcp-weather-advisor/
├── server.py                 ← the file Claude Desktop runs (a small launcher)
├── mcp_weather_advisor/      ← the server package
│   ├── __init__.py           version number
│   ├── __main__.py           enables `python -m mcp_weather_advisor`
│   └── server.py             the server: settings, logging, cache, helpers, the four tools
├── tests/                    pytest suite (fake APIs; no internet needed)
│   ├── conftest.py           fixtures: starts the server over stdio
│   ├── fake_api.py           local stand-in for Open-Meteo / Transitous / LTA
│   ├── sample_data.py        realistic API responses for the tests
│   └── test_*.py             the tests (see Testing)
├── pyproject.toml            packaging: dependencies, `mcp-weather-advisor` command, pytest settings
├── requirements.txt          shortcut for `pip install -e .`
├── requirements-dev.txt      shortcut for `pip install -e ".[dev]"`
├── README.md
├── LICENSE                   MIT
└── .venv/                    your virtual environment (created by you, not in git)
```

`mcp_weather_advisor/server.py` is organised in labelled sections: settings, logging, cache, helpers, the four tools (decorated with `@mcp.tool()`) and the entry point. Each tool's docstring is what Claude reads to decide when to use it.

## Where files are stored

The cache (`cache.sqlite3`) and log (`mcp_server.log`) live in **`~/.mcp-weather-advisor/`**, created on first use. They're kept outside the code folder because an installed package's folder may be read-only. Set `WEATHER_ADVISOR_HOME` to use another folder, or move each file individually with `WEATHER_ADVISOR_CACHE` and `WEATHER_ADVISOR_LOG`.

## Logging

Problems are written to `~/.mcp-weather-advisor/mcp_server.log` as JSON Lines, one JSON object per line:

```json
{"ts": "2026-11-10T03:29:38.790Z", "level": "ERROR", "event": "api_timeout", "tool": "get_current_weather", "call_id": "27efa384", "url": "https://geocoding-api.open-meteo.com/v1/search", "params": {"name": "tokyo", "count": 1, "language": "en"}, "timeout_s": 20.0, "elapsed_ms": 20004, "error": "ReadTimeout"}
```

Every record has `ts` (UTC), `level` and `event`. Records made during a tool call also carry `tool` and a `call_id`, so you can group everything from one call.

| Event | Level | When |
|-------|-------|------|
| `api_timeout` | ERROR | An API didn't answer within the timeout |
| `api_http_error` | ERROR | An API returned HTTP 4xx/5xx (includes `status` and the first 200 characters of the body) |
| `api_unreachable` | ERROR | Connection failed (DNS, refused, network down) |
| `api_invalid_json` | ERROR | An API answered with something that isn't JSON |
| `api_bad_shape` | ERROR | Valid JSON, but missing expected fields (includes a traceback) |
| `bad_coordinates` | ERROR | A geocoder returned missing or impossible latitude/longitude |
| `location_not_found` | WARNING | No place/stop matched the query |
| `invalid_input` | WARNING | Blank or overlong input |
| `cache_unavailable` / `cache_error` | WARNING | The cache file couldn't be opened, read or written |
| `tool_crashed` | ERROR | Any other unexpected exception (includes a traceback) |
| `server_start` / `server_stop` | INFO | Server lifecycle |

- **Nothing is ever printed to stdout.** stdout carries the MCP protocol, and the test suite checks it stays clean while every failure type is logged.
- **No failure crashes a tool.** Every one becomes a readable message for Claude, such as *"Could not get weather for 'Tokyo': the service did not respond within 20 seconds."*
- **The log rotates** at about 1 MB, keeping `mcp_server.log.1` to `.3`.
- **API keys are never logged.** Request headers are not recorded, and query parameters with names like `key`, `token` or `secret` are redacted.
- `WEATHER_ADVISOR_LOG=/path/to/file.log` moves the log; `WEATHER_ADVISOR_LOG=off` disables it. If the file can't be opened, log lines go to stderr instead, which Claude Desktop saves in its own MCP logs.
- `WEATHER_ADVISOR_TIMEOUT` sets the network timeout in seconds (default 20).

Quick look at recent errors (needs `jq`):
```bash
jq -c 'select(.level != "INFO") | {ts, event, tool, error, status}' ~/.mcp-weather-advisor/mcp_server.log
```

## Testing

```bash
pip install -e ".[dev]"
python -m pytest
```

The suite (~255 tests, about 90 seconds) needs no internet and no API keys:

- **Over stdio, like Claude Desktop:** almost every test starts the installed server as a subprocess and calls its tools with the official MCP client.
- **Against a local fake API:** each test scripts the fake's replies, via the environment variables `OPEN_METEO_GEOCODING_URL`, `OPEN_METEO_FORECAST_URL`, `TRANSITOUS_URL` and `LTA_BUS_ARRIVAL_URL`.

| File | Covers |
|------|--------|
| `tests/test_protocol.py` | Tool discovery, schemas, unknown tools, missing/wrong-type arguments |
| `tests/test_weather.py` | Weather and geocoding output, unknown/blank/overlong/Unicode places, query-string injection |
| `tests/test_commute.py` | Trip ranking in good vs bad weather, local times, departed trips, no-route and bad-timezone cases |
| `tests/test_departures.py` | Stop codes vs names, limit clamping, LTA live arrivals, LTA auth/rate-limit errors |
| `tests/test_api_failures.py` | Every tool × every upstream API returning HTTP 4xx/5xx, HTML, invalid JSON, empty bodies, wrong JSON shapes, timeouts, or being unreachable |
| `tests/test_cache.py` | Cache hits, TTLs, expiry, departed-trip refresh, restart persistence, corrupt/unwritable cache files, keys never stored |
| `tests/test_logging.py` | Every failure type writes one valid JSON log record with the right fields; raw stdout stays pure JSON-RPC; key redaction, rotation, unwritable/disabled log |
| `tests/test_packaging.py` | Package metadata, the `mcp-weather-advisor` command, `python -m` and `python server.py` launch, running from any folder, default file locations |
| `tests/test_helpers.py` | Fast in-process unit tests of weather thresholds, time conversion and cache primitives |

### Continuous integration

`.github/workflows/tests.yml` runs the whole suite on GitHub Actions for every push and pull request (and on demand from the **Actions** tab), on **Python 3.11 and 3.12** on Ubuntu. Each run:

1. installs the package with `pip install -e ".[dev]"`, exactly as a developer would;
2. runs the **SQLite cache tests** (`tests/test_cache.py`) as their own step;
3. runs **everything else**: the MCP stdio tool tests, API-failure handling, logging, packaging and unit tests. This step runs even if the cache step failed, so all problems show up in one run.

If anything fails, the run keeps a downloadable artifact with JUnit reports plus each test's server stderr and `mcp_server.log`. No secrets are needed: the tests use a local fake API.

Replace `<your-username>` in the badge at the top of this README once the repository is on GitHub.

## Troubleshooting

- **Server doesn't appear in Claude:** see [Common mistakes](#common-mistakes).
- **`ModuleNotFoundError`:** run `pip install -e .` with the `.venv` activated, and make sure `command` is that `.venv`'s Python (see [Common mistakes](#common-mistakes)).
- **`mcp-weather-advisor: command not found`:** the environment's `bin` (or `Scripts`) folder isn't on your `PATH`. Activate the virtual environment, or use the full path.
- **"No location found":** try a larger nearby city or add the country.
- **"No public-transport route found":** the region may not publish open transit data on Transitous.
- **LTA errors (401):** the `LTA_ACCOUNT_KEY` is missing or wrong.

## License

MIT. Weather data by [Open-Meteo.com](https://open-meteo.com) (CC BY 4.0). Transit routing by [Transitous](https://transitous.org) using open GTFS data from each operator. Singapore bus arrivals from LTA DataMall under the [Singapore Open Data Licence](https://data.gov.sg/open-data-licence).
