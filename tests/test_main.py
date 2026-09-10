import importlib.util
import runpy
from pathlib import Path
from unittest.mock import patch

from electiondata_my_mcp import server

SERVER_PATH = Path(__file__).resolve().parents[1] / "src" / "electiondata_my_mcp" / "server.py"


def test_server_main_runs_mcp() -> None:
    with patch.object(server.mcp, "run") as mock_run:
        server.main()
    mock_run.assert_called_once()


def test_package_main_entrypoint() -> None:
    main_path = Path(__file__).resolve().parents[1] / "src" / "electiondata_my_mcp" / "__main__.py"
    with patch.object(server, "main") as mock_main:
        runpy.run_path(str(main_path), run_name="__main__")
    mock_main.assert_called_once()


def test_module_loads_without_sys_modules_entry() -> None:
    """`mcp dev` execs server.py without registering it, so hints must resolve eagerly."""
    spec = importlib.util.spec_from_file_location("electiondata_server_probe", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert module.mcp is not server.mcp
