# MCP Weather & Transit Advisor

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

## Setup (beginner-friendly)

### 1. Install Python 3.10 or newer
- **macOS:** download from <https://www.python.org/downloads/> (or `brew install python`).
- **Windows:** download from the same page and tick **"Add python.exe to PATH"** in the installer.
- Check it worked: `python3 --version` (on Windows: `python --version`).

`pip` (Python's package installer) comes with Python. Check with `pip3 --version`.

### 2. Get the project and create a virtual environment
A virtual environment keeps this project's packages separate from the rest of your computer.

```bash
cd mcp-weather-advisor
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 3. Install the dependencies
```bash
pip install -r requirements.txt
```
This installs the `mcp` framework and `httpx` (for web requests).

> **Apple Silicon / Intel Mac note:** if the install fails while building `cryptography`, use
> `pip install --only-binary cryptography -r requirements.txt` to fetch a prebuilt version.

### 4. Try it
```bash
python server.py
```
The server waits silently for Claude to talk to it (press `Ctrl+C` to stop). To test interactively with the MCP Inspector:
```bash
mcp dev server.py
```

## Connect to Claude Desktop

1. Open Claude Desktop, then **Settings → Developer → Edit Config**. This opens `claude_desktop_config.json`.
2. Add the server below. Use **absolute paths**, and point `command` at the Python inside your `.venv` so it finds the installed packages:

```json
{
  "mcpServers": {
    "weather-transit-advisor": {
      "command": "/absolute/path/to/mcp-weather-advisor/.venv/bin/python",
      "args": ["/absolute/path/to/mcp-weather-advisor/server.py"],
      "env": {
        "LTA_ACCOUNT_KEY": "your-lta-key-here"
      }
    }
  }
}
```
   The `env` block is optional. Delete it if you don't have an LTA key; otherwise a placeholder value will cause 401 errors for Singapore stop codes.
   On Windows the command is `C:\\path\\to\\mcp-weather-advisor\\.venv\\Scripts\\python.exe`.

3. Fully quit and reopen Claude Desktop.
4. Try asking:
   - *"What's the weather in Lisbon, and should I cycle to work?"*
   - *"How do I get from Orchard MRT to Changi Airport right now?"*
   - *"When's the next bus at stop 83139?"*

## How it works

`server.py` is organised in four labelled sections: server creation, helper functions, the four tools (decorated with `@mcp.tool()`), and the entry point. Each tool's docstring is what Claude reads to decide when to use it.

## Troubleshooting

- **Server doesn't appear in Claude:** check the paths are absolute and the JSON is valid, then fully restart Claude Desktop.
- **`ModuleNotFoundError: mcp`:** `command` isn't pointing at the `.venv` Python.
- **"No location found":** try a larger nearby city or add the country.
- **"No public-transport route found":** the region may not publish open transit data on Transitous.
- **LTA errors (401):** the `LTA_ACCOUNT_KEY` is missing or wrong.

## License

MIT. Weather data by [Open-Meteo.com](https://open-meteo.com) (CC BY 4.0). Transit routing by [Transitous](https://transitous.org) using open GTFS data from each operator. Singapore bus arrivals from LTA DataMall under the [Singapore Open Data Licence](https://data.gov.sg/open-data-licence).
