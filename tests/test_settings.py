from __future__ import annotations

import re

import pytest

from electiondata_my_mcp.settings import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_ALLOWED_ORIGINS,
    ENV_ALLOWED_HOSTS,
    ENV_ALLOWED_ORIGINS,
    ENV_DNS_REBINDING,
    apply_cli_overrides,
    cors_origin_config,
    load_http_settings,
)


def test_load_http_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ALLOWED_HOSTS, raising=False)
    monkeypatch.delenv(ENV_ALLOWED_ORIGINS, raising=False)
    monkeypatch.delenv(ENV_DNS_REBINDING, raising=False)
    settings = load_http_settings()
    assert settings.allowed_hosts == list(DEFAULT_ALLOWED_HOSTS)
    assert settings.allowed_origins == list(DEFAULT_ALLOWED_ORIGINS)
    assert settings.enable_dns_rebinding_protection is True


def test_load_http_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ALLOWED_HOSTS, "mcp.example.com, mcp.example.com:*")
    monkeypatch.setenv(ENV_ALLOWED_ORIGINS, "https://app.example.com")
    monkeypatch.setenv(ENV_DNS_REBINDING, "off")
    settings = load_http_settings()
    assert settings.allowed_hosts == ["mcp.example.com", "mcp.example.com:*"]
    assert settings.allowed_origins == ["https://app.example.com"]
    assert settings.enable_dns_rebinding_protection is False


def test_apply_cli_overrides_writes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ALLOWED_HOSTS, raising=False)
    monkeypatch.delenv(ENV_ALLOWED_ORIGINS, raising=False)
    monkeypatch.delenv(ENV_DNS_REBINDING, raising=False)
    apply_cli_overrides(
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://app.example.com"],
        enable_dns_rebinding_protection=False,
    )
    settings = load_http_settings()
    assert settings.allowed_hosts == ["mcp.example.com"]
    assert settings.allowed_origins == ["https://app.example.com"]
    assert settings.enable_dns_rebinding_protection is False


def test_apply_cli_overrides_can_enable_dns_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DNS_REBINDING, "false")
    apply_cli_overrides(enable_dns_rebinding_protection=True)
    assert load_http_settings().enable_dns_rebinding_protection is True


def test_cors_origin_config_wildcard_ports() -> None:
    exact, regex = cors_origin_config(
        ["http://127.0.0.1:*", "http://localhost:*", "https://app.example.com"]
    )
    assert exact == ["https://app.example.com"]
    assert regex is not None
    compiled = re.compile(regex)
    assert compiled.fullmatch("http://localhost:3000")
    assert compiled.fullmatch("http://127.0.0.1")
    assert compiled.fullmatch("https://app.example.com") is None


def test_cors_origin_config_star() -> None:
    exact, regex = cors_origin_config(["*"])
    assert exact == ["*"]
    assert regex is None
