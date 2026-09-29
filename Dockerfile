# Streamable HTTP production image.
#
# The dependency layer is installed before the source copy so a code-only
# change does not rebuild the virtualenv from scratch. The project itself is
# installed on the second `uv sync` (the first pass has no `src/` yet).
#
# Build (OCI version label from pyproject.toml via uv):
#   docker build --build-arg VERSION="$(uv version --short)" -t electiondata-my-mcp .
# Run:   docker run --rm -p 8000:8000 electiondata-my-mcp

# Both stages must use the same interpreter: the venv links to
# /usr/local/bin/python3.12 and its .pyc files are compiled by the build stage.
ARG PYTHON_VERSION=3.12.14

FROM python:${PYTHON_VERSION}-bookworm AS base

COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /uvx /bin/

WORKDIR /app

# UV_COMPILE_BYTECODE for generating .pyc files -> faster application startup.
# UV_LINK_MODE=copy to silence warnings about not being able to use hard links
# since the build stage cache and sync target are on separate file systems.
# UV_PYTHON_DOWNLOADS=0 so uv fails rather than build the venv on a managed
# interpreter that the final stage does not have.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

# Install third-party dependencies. Bind-mount the lockfiles so this layer
# stays cached when only application source changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=/app/uv.lock \
    --mount=type=bind,source=pyproject.toml,target=/app/pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# README is part of the project metadata hatchling reads while building the wheel.
COPY pyproject.toml uv.lock README.md ./
COPY src /app/src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-bookworm AS final

# DuckDB installs httpfs into ~/.duckdb/extensions on first query and the prompt
# loader caches under ~/.cache, so the runtime user needs a writable home.
RUN useradd --create-home --uid 10001 appuser

EXPOSE 8000

# PYTHONUNBUFFERED=1 to disable output buffering.
# WEB_CONCURRENCY is uvicorn's worker count when --workers is not passed.
ENV PYTHONUNBUFFERED=1 WEB_CONCURRENCY=4

ARG VERSION
LABEL org.opencontainers.image.version=$VERSION

WORKDIR /app

COPY --from=base /app /app

ENV PATH="/app/.venv/bin:$PATH"

# /app stays root-owned and read-only to the app. Numeric so Kubernetes
# runAsNonRoot can verify it.
USER 10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "electiondata_my_mcp.http_app:app", "--host", "0.0.0.0", "--port", "8000"]
