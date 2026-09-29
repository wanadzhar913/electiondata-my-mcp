"""ElectionData.MY MCP server: query the public Malaysian election data lake with DuckDB SQL."""

from importlib.metadata import PackageNotFoundError, version


def _package_version() -> str:
    try:
        return version("electiondata-my-mcp")
    except PackageNotFoundError:
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        return tomllib.loads(pyproject.read_bytes())["project"]["version"]


__version__ = _package_version()
