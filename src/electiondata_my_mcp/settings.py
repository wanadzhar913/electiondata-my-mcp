"""Streamable HTTP settings from the environment.

CLI flags in ``server.main`` write these variables before uvicorn forks
workers, so each worker re-imports ``http_app`` and sees the same values.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

ENV_ALLOWED_HOSTS = "MCP_ALLOWED_HOSTS"
ENV_ALLOWED_ORIGINS = "MCP_ALLOWED_ORIGINS"
ENV_DNS_REBINDING = "MCP_ENABLE_DNS_REBINDING_PROTECTION"

DEFAULT_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
)

_FALSEY = {"0", "false", "no", "off"}


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_bool(value: str) -> bool:
    return value.strip().lower() not in _FALSEY


@dataclass(frozen=True)
class HttpSettings:
    allowed_hosts: list[str]
    allowed_origins: list[str]
    enable_dns_rebinding_protection: bool


def load_http_settings() -> HttpSettings:
    """Read Host/Origin allowlists from the environment, with localhost defaults."""
    hosts_raw = os.environ.get(ENV_ALLOWED_HOSTS)
    origins_raw = os.environ.get(ENV_ALLOWED_ORIGINS)
    dns_raw = os.environ.get(ENV_DNS_REBINDING)
    return HttpSettings(
        allowed_hosts=_split_csv(hosts_raw) if hosts_raw else list(DEFAULT_ALLOWED_HOSTS),
        allowed_origins=_split_csv(origins_raw) if origins_raw else list(DEFAULT_ALLOWED_ORIGINS),
        enable_dns_rebinding_protection=_parse_bool(dns_raw) if dns_raw is not None else True,
    )


def apply_cli_overrides(
    *,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    enable_dns_rebinding_protection: bool | None = None,
) -> None:
    """Copy CLI allowlists into the environment so uvicorn workers inherit them."""
    if allowed_hosts:
        os.environ[ENV_ALLOWED_HOSTS] = ",".join(allowed_hosts)
    if allowed_origins:
        os.environ[ENV_ALLOWED_ORIGINS] = ",".join(allowed_origins)
    if enable_dns_rebinding_protection is False:
        os.environ[ENV_DNS_REBINDING] = "false"
    elif enable_dns_rebinding_protection is True:
        os.environ[ENV_DNS_REBINDING] = "true"


def cors_origin_config(origins: list[str]) -> tuple[list[str], str | None]:
    """Split exact CORS origins from ``scheme://host:*`` patterns used by transport security.

    Starlette matches ``allow_origins`` exactly. The MCP Host/Origin allowlist treats a
    trailing ``:*`` as "any port", so those entries become ``allow_origin_regex``.
    """
    if "*" in origins:
        return ["*"], None

    exact: list[str] = []
    regex_parts: list[str] = []
    for origin in origins:
        if origin.endswith(":*"):
            prefix = origin[:-2]
            regex_parts.append(re.escape(prefix) + r"(?::\d+)?")
        else:
            exact.append(origin)
    if not regex_parts:
        return exact, None
    return exact, r"^(?:" + "|".join(regex_parts) + r")$"
