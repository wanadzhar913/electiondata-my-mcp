"""Bounded DuckDB cursor pool over a single per-process database.

One ``connect()`` owns the lake views. Concurrent queries check out
``connection.cursor()`` instances — DuckDB connections are not safe to share
across threads, but cursors over the same database are. When every cursor is
busy, callers wait on the idle queue; after ``checkout_timeout`` they get
``PoolTimeoutError`` instead of holding an anyio worker thread forever.
"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import duckdb

from electiondata_my_mcp.duckdb_lake import connect as default_connect

DEFAULT_POOL_SIZE = 4
DEFAULT_CHECKOUT_TIMEOUT = 10.0
ENV_POOL_SIZE = "MCP_DUCKDB_POOL_SIZE"
ENV_POOL_TIMEOUT = "MCP_DUCKDB_POOL_TIMEOUT"

ConnectFn = Callable[[], duckdb.DuckDBPyConnection]


class PoolTimeoutError(TimeoutError):
    """No cursor became free within ``checkout_timeout``."""


class DuckDBPool:
    """Lazy, bounded set of cursors over one DuckDB database per process."""

    def __init__(
        self,
        size: int = DEFAULT_POOL_SIZE,
        checkout_timeout: float = DEFAULT_CHECKOUT_TIMEOUT,
        *,
        connect_fn: ConnectFn = default_connect,
    ) -> None:
        if size < 1:
            raise ValueError("pool size must be at least 1")
        if checkout_timeout <= 0:
            raise ValueError("checkout_timeout must be positive")
        self._size = size
        self._timeout = checkout_timeout
        self._connect = connect_fn
        self._root: duckdb.DuckDBPyConnection | None = None
        self._idle: queue.LifoQueue[duckdb.DuckDBPyConnection] = queue.LifoQueue()
        self._lock = threading.Lock()
        self._created = 0

    @property
    def size(self) -> int:
        return self._size

    @property
    def checkout_timeout(self) -> float:
        return self._timeout

    @property
    def created(self) -> int:
        return self._created

    @contextmanager
    def acquire(self) -> Iterator[duckdb.DuckDBPyConnection]:
        cursor = self._checkout()
        try:
            yield cursor
        finally:
            self._idle.put(cursor)

    def _checkout(self) -> duckdb.DuckDBPyConnection:
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass

        with self._lock:
            if self._root is None:
                self._root = self._connect()
            if self._created < self._size:
                self._created += 1
                try:
                    return self._root.cursor()
                except Exception:
                    self._created -= 1
                    raise

        try:
            return self._idle.get(timeout=self._timeout)
        except queue.Empty:
            raise PoolTimeoutError(f"no DuckDB cursor free within {self._timeout:.1f}s") from None


def pool_from_env(*, connect_fn: ConnectFn = default_connect) -> DuckDBPool:
    """Build a pool from ``MCP_DUCKDB_POOL_SIZE`` / ``MCP_DUCKDB_POOL_TIMEOUT``."""
    size_raw = os.environ.get(ENV_POOL_SIZE)
    timeout_raw = os.environ.get(ENV_POOL_TIMEOUT)
    try:
        size = int(size_raw) if size_raw else DEFAULT_POOL_SIZE
    except ValueError as error:
        raise ValueError(f"{ENV_POOL_SIZE} must be an integer, got {size_raw!r}") from error
    try:
        timeout = float(timeout_raw) if timeout_raw else DEFAULT_CHECKOUT_TIMEOUT
    except ValueError as error:
        raise ValueError(f"{ENV_POOL_TIMEOUT} must be a number, got {timeout_raw!r}") from error
    return DuckDBPool(size=size, checkout_timeout=timeout, connect_fn=connect_fn)
