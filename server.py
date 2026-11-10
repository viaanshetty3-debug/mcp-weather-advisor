"""Convenience launcher: `python server.py` from the repository folder.

The real code is in mcp_weather_advisor/server.py. After `pip install -e .`
you can also start it with the `mcp-weather-advisor` command.
"""

from mcp_weather_advisor.server import main

if __name__ == "__main__":
    main()
