# MCP Weather & Commute Advisor

A small [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude look up real-time weather for any place in the world, using the free [Open-Meteo](https://open-meteo.com) API (no API key needed).

## Tools

| Tool | What it does |
|------|--------------|
| `geocode_location` | Converts a place name into latitude, longitude and timezone. |
| `get_current_weather` | Current conditions: temperature, feels-like, humidity, precipitation, wind. |
| `get_commute_advice` | Rates walking, cycling and public transit for the current weather and lists warnings (rain, wind, ice, heat). |

> **Scope note:** Open-Meteo has no bus or train data. `get_commute_advice` is based on weather only. It does not know timetables, routes or delays.

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
      "args": ["/absolute/path/to/mcp-weather-advisor/server.py"]
    }
  }
}
```
   On Windows the command is `C:\\path\\to\\mcp-weather-advisor\\.venv\\Scripts\\python.exe`.

3. Fully quit and reopen Claude Desktop.
4. Try asking: *"What's the weather in Lisbon, and should I cycle to work?"*

## How it works

`server.py` is organised in four labelled sections: server creation, helper functions, the three tools (decorated with `@mcp.tool()`), and the entry point. Each tool's docstring is what Claude reads to decide when to use it.

## Troubleshooting

- **Server doesn't appear in Claude:** check the paths are absolute and the JSON is valid, then fully restart Claude Desktop.
- **`ModuleNotFoundError: mcp`:** `command` isn't pointing at the `.venv` Python.
- **"No location found":** try a larger nearby city or add the country.

## License

MIT. Weather data by [Open-Meteo.com](https://open-meteo.com) (CC BY 4.0).
