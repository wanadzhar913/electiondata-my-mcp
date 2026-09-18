from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from electiondata_my_mcp.server import ASGI_APP_IMPORT_STRING, main, parse_args
from electiondata_my_mcp.settings import ENV_ALLOWED_HOSTS, ENV_ALLOWED_ORIGINS, ENV_DNS_REBINDING

pytestmark = pytest.mark.unit

def test_parse_args_defaults_to_stdio() -> None:
    args = parse_args([])
    assert args.transport == "stdio"
    assert args.host is None
    assert args.port is None
    assert args.workers is None
    assert args.allowed_hosts is None
    assert args.allowed_origins is None
    assert args.disable_dns_rebinding_protection is False


@pytest.mark.parametrize(
    "argv",
    [
        ["--host", "0.0.0.0"],
        ["--port", "8000"],
        ["--workers", "2"],
        ["--allowed-host", "mcp.example.com"],
        ["--allowed-origin", "https://app.example.com"],
        ["--disable-dns-rebinding-protection"],
    ],
)
def test_http_flags_rejected_on_stdio(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_streamable_http_defaults() -> None:
    args = parse_args(["--transport", "streamable-http"])
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.workers == 1


def test_streamable_http_rejects_invalid_workers() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--transport", "streamable-http", "--workers", "0"])


def test_streamable_http_rejects_invalid_port() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--transport", "streamable-http", "--port", "0"])


def test_http_main_runs_uvicorn_with_import_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ALLOWED_HOSTS, "")
    monkeypatch.setenv(ENV_ALLOWED_ORIGINS, "")
    monkeypatch.setenv(ENV_DNS_REBINDING, "true")

    with patch("uvicorn.run") as mock_run:
        main(
            [
                "--transport",
                "streamable-http",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--workers",
                "4",
                "--allowed-host",
                "mcp.example.com",
                "--allowed-host",
                "mcp.example.com:*",
                "--allowed-origin",
                "https://app.example.com",
            ]
        )

    mock_run.assert_called_once_with(
        ASGI_APP_IMPORT_STRING,
        host="0.0.0.0",
        port=9000,
        workers=4,
    )
    assert os.environ[ENV_ALLOWED_HOSTS] == "mcp.example.com,mcp.example.com:*"
    assert os.environ[ENV_ALLOWED_ORIGINS] == "https://app.example.com"
    assert os.environ[ENV_DNS_REBINDING] == "true"


def test_http_main_disable_dns_rebinding_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DNS_REBINDING, "true")
    with patch("uvicorn.run"):
        main(["--transport", "streamable-http", "--disable-dns-rebinding-protection"])
    assert os.environ[ENV_DNS_REBINDING] == "false"
