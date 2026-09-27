# Streamable HTTP production image.
#
# The dependency layer is installed before the source copy so a code-only
# change does not rebuild the virtualenv from scratch. The project itself is
# installed on the second `uv sync` (the first pass has no `src/` yet).
#
# Build: docker build -t electiondata-my-mcp .
# Run:   docker run --rm -p 8000:8000 electiondata-my-mcp

FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS base

WORKDIR /app

# UV_COMPILE_BYTECODE for generating .pyc files -> faster application startup.
# UV_LINK_MODE=copy to silence warnings about not being able to use hard links
# since the cache and sync target are on separate file systems.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

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

FROM python:3.12.8-slim AS final

EXPOSE 8000

# PYTHONUNBUFFERED=1 to disable output buffering
ENV PYTHONUNBUFFERED=1
ARG VERSION=0.2.0
ENV APP_VERSION=$VERSION

WORKDIR /app

# Copy the virtual environment from the base stage. Both stages are CPython 3.12
# on glibc, so the venv's interpreter path resolves in this image.
COPY --from=base /app /app

# Add virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

# The ASGI app is electiondata_my_mcp.http_app:app (stateless Streamable HTTP).
CMD ["uvicorn", "electiondata_my_mcp.http_app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
