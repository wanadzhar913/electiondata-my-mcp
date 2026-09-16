"""ASGI app for Streamable HTTP.

Import this module (or ``uvicorn electiondata_my_mcp.http_app:app``) rather than
calling ``mcp.run("streamable-http")``. The host Starlette wraps
``mcp.streamable_http_app()`` so CORS and the session-manager lifespan run on
the app that actually serves requests. Stdio clients should keep importing
``server`` only — they never need this module.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from electiondata_my_mcp.server import mcp
from electiondata_my_mcp.settings import cors_origin_config, load_http_settings

CORS_ALLOW_METHODS = ["GET", "POST", "DELETE"]
CORS_ALLOW_HEADERS = [
    "Authorization",
    "Content-Type",
    "Last-Event-ID",
    "Mcp-Method",
    "Mcp-Name",
    "Mcp-Protocol-Version",
    "Mcp-Session-Id",
]
CORS_EXPOSE_HEADERS = ["Mcp-Session-Id"]


def build_asgi_app() -> Starlette:
    """Build the host Starlette app from the current process environment."""
    settings = load_http_settings()
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.enable_dns_rebinding_protection,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )
    inner = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=security,
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    exact_origins, origin_regex = cors_origin_config(settings.allowed_origins)
    cors_kwargs: dict[str, object] = {
        "allow_origins": exact_origins,
        "allow_methods": CORS_ALLOW_METHODS,
        "allow_headers": CORS_ALLOW_HEADERS,
        "expose_headers": CORS_EXPOSE_HEADERS,
    }
    if origin_regex is not None:
        cors_kwargs["allow_origin_regex"] = origin_regex

    app = Starlette(
        routes=[Mount("/", app=inner)],
        middleware=[Middleware(CORSMiddleware, **cors_kwargs)],
        lifespan=lifespan,
    )
    app.state.http_settings = settings
    app.state.transport_security = security
    return app


app = build_asgi_app()
