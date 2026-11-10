"""The installed package: entry points, metadata and default file locations.

Requires the project to be installed (`pip install -e ".[dev]"`).
"""

import shutil
import sys

from importlib.metadata import entry_points, metadata, version
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # stdlib on Python 3.11+

import fake_api as fa  # noqa: E402
import mcp_weather_advisor  # noqa: E402
from conftest import REPO, launch  # noqa: E402

pytestmark = pytest.mark.anyio

PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text())


def installed_command() -> str:
    """Path of the `mcp-weather-advisor` script next to this Python."""
    name = "mcp-weather-advisor" + (".exe" if sys.platform == "win32" else "")
    path = Path(sys.executable).parent / name
    if not path.exists():
        path = Path(shutil.which("mcp-weather-advisor") or path)
    assert path.exists(), "run `pip install -e .` first"
    return str(path)


# ---- metadata ---------------------------------------------------------------
def test_installed_version_matches_package():
    assert version("mcp-weather-advisor") == mcp_weather_advisor.__version__


def test_metadata_declares_dependencies_and_python():
    meta = metadata("mcp-weather-advisor")
    requires = meta.get_all("Requires-Dist")
    assert any(r.startswith("mcp") and "<3" in r for r in requires)
    assert any(r.startswith("httpx") for r in requires)
    assert meta["Requires-Python"] == ">=3.10"
    assert meta["License-Expression"] == "MIT"


def test_console_script_is_registered():
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}
    assert scripts["mcp-weather-advisor"] == "mcp_weather_advisor.server:main"


def test_no_generic_top_level_server_module_is_installed():
    """Installing must not claim the importable name `server` globally."""
    assert PYPROJECT["tool"]["setuptools"]["packages"] == ["mcp_weather_advisor"]
    assert "py-modules" not in PYPROJECT["tool"]["setuptools"]


def test_runtime_dependencies_match_what_the_code_imports():
    """Everything server.py imports is stdlib or a declared dependency."""
    deps = PYPROJECT["project"]["dependencies"]
    # Platform-specific pins (after ";") are install fixes, not imports.
    direct = {d.split(">")[0].split("<")[0].split("=")[0].strip()
              for d in deps if ";" not in d}
    assert direct == {"mcp", "httpx"}
    source = (REPO / "mcp_weather_advisor" / "server.py").read_text()
    third_party = {line.split()[1].split(".")[0]
                   for line in source.splitlines()
                   if line.startswith(("import ", "from "))
                   and line.split()[1].split(".")[0]
                   not in sys.stdlib_module_names}
    assert third_party == {"httpx", "mcp", "mcp_weather_advisor"}


# ---- every way of starting the server works ---------------------------------
@pytest.fixture(params=["console-script", "python -m", "repo server.py"])
def start_command(request):
    return {
        "console-script": lambda: [installed_command()],
        "python -m": lambda: [sys.executable, "-m", "mcp_weather_advisor"],
        "repo server.py": lambda: [sys.executable, str(REPO / "server.py")],
    }[request.param]()


async def test_server_starts_every_way(server_env, sg_api, tmp_path,
                                       start_command):
    async with launch(server_env, tmp_path / "stderr",
                      command=start_command) as client:
        tools = (await client.session.list_tools()).tools
        text = await client.call("get_current_weather", place="Singapore")
    assert len(tools) == 4
    assert "Weather in Singapore" in text


async def test_runs_from_any_working_directory(server_env, sg_api, tmp_path):
    """A globally installed command must not depend on the current folder."""
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=installed_command(),
                                   env=server_env, cwd=str(elsewhere))
    with open(tmp_path / "stderr", "w") as err:
        async with stdio_client(params, errlog=err) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "get_current_weather", {"place": "Singapore"})
    assert "Weather in Singapore" in result.content[0].text


# ---- default cache/log location ---------------------------------------------
async def test_cache_and_log_default_to_user_data_dir(server_env, sg_api,
                                                      tmp_path):
    """Without overrides, files go to WEATHER_ADVISOR_HOME (default
    ~/.mcp-weather-advisor) - never into the installed package folder."""
    home = tmp_path / "data-home"          # doesn't exist yet
    env = {k: v for k, v in server_env.items()
           if k not in ("WEATHER_ADVISOR_CACHE", "WEATHER_ADVISOR_LOG")}
    env["WEATHER_ADVISOR_HOME"] = str(home)
    sg_api.json(fa.FORECAST, {}, status=500)   # produce a log entry
    async with launch(env, tmp_path / "stderr") as client:
        await client.call("get_current_weather", place="Singapore")
    assert (home / "cache.sqlite3").exists()
    assert "api_http_error" in (home / "mcp_server.log").read_text()
    package_dir = Path(mcp_weather_advisor.__file__).parent
    assert not list(package_dir.glob("*.sqlite3"))
    assert not list(package_dir.glob("*.log"))


async def test_default_home_is_dot_folder_in_user_home(server_env, sg_api,
                                                       tmp_path):
    env = {k: v for k, v in server_env.items()
           if k not in ("WEATHER_ADVISOR_CACHE", "WEATHER_ADVISOR_LOG")}
    env["HOME"] = str(tmp_path)
    async with launch(env, tmp_path / "stderr") as client:
        await client.call("get_current_weather", place="Singapore")
    assert (tmp_path / ".mcp-weather-advisor" / "cache.sqlite3").exists()
