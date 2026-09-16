from __future__ import annotations

import threading
from unittest.mock import MagicMock

import duckdb
import pytest

from electiondata_my_mcp.duckdb_pool import (
    DEFAULT_CHECKOUT_TIMEOUT,
    DEFAULT_POOL_SIZE,
    ENV_POOL_SIZE,
    ENV_POOL_TIMEOUT,
    DuckDBPool,
    PoolTimeoutError,
    pool_from_env,
)


def _memory_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT range AS i FROM range(1000)")
    return con


def test_pool_rejects_invalid_size() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        DuckDBPool(size=0, connect_fn=_memory_db)


def test_pool_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        DuckDBPool(checkout_timeout=0, connect_fn=_memory_db)


def test_connect_is_lazy_and_once() -> None:
    connect_fn = MagicMock(side_effect=_memory_db)
    pool = DuckDBPool(size=2, connect_fn=connect_fn)
    assert pool.created == 0
    connect_fn.assert_not_called()

    with pool.acquire() as first:
        first.sql("SELECT count(*) FROM t").fetchone()
    with pool.acquire() as second:
        second.sql("SELECT count(*) FROM t").fetchone()

    connect_fn.assert_called_once()
    assert pool.created == 1
    assert second is first


def test_pool_creates_up_to_size_cursors() -> None:
    pool = DuckDBPool(size=2, checkout_timeout=1.0, connect_fn=_memory_db)
    with pool.acquire() as a:
        with pool.acquire() as b:
            assert a is not b
            assert pool.created == 2
            a.sql("SELECT 1").fetchone()
            b.sql("SELECT 1").fetchone()


def test_acquire_returns_cursor_after_exception() -> None:
    pool = DuckDBPool(size=1, connect_fn=_memory_db)
    with pytest.raises(RuntimeError, match="boom"):
        with pool.acquire():
            raise RuntimeError("boom")
    with pool.acquire() as con:
        assert con.sql("SELECT count(*) FROM t").fetchone() == (1000,)


def test_checkout_times_out_when_pool_is_busy() -> None:
    pool = DuckDBPool(size=1, checkout_timeout=0.05, connect_fn=_memory_db)
    started = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with pool.acquire():
            started.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert started.wait(timeout=5)
    with pytest.raises(PoolTimeoutError, match="no DuckDB cursor free"):
        with pool.acquire():
            pass
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_concurrent_queries_share_one_database() -> None:
    pool = DuckDBPool(size=3, checkout_timeout=5.0, connect_fn=_memory_db)
    results: list[int] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with pool.acquire() as con:
                count = con.sql("SELECT count(*) FROM t").fetchone()[0]
                results.append(count)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert results == [1000] * 6
    assert pool.created <= 3


def test_cursor_failure_does_not_consume_a_slot() -> None:
    root = MagicMock()
    root.cursor.side_effect = RuntimeError("cannot cursor")
    pool = DuckDBPool(size=2, connect_fn=lambda: root)
    with pytest.raises(RuntimeError, match="cannot cursor"):
        with pool.acquire():
            pass
    assert pool.created == 0


def test_pool_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_POOL_SIZE, raising=False)
    monkeypatch.delenv(ENV_POOL_TIMEOUT, raising=False)
    pool = pool_from_env(connect_fn=_memory_db)
    assert pool.size == DEFAULT_POOL_SIZE
    assert pool.checkout_timeout == DEFAULT_CHECKOUT_TIMEOUT


def test_pool_from_env_reads_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_POOL_SIZE, "8")
    monkeypatch.setenv(ENV_POOL_TIMEOUT, "2.5")
    pool = pool_from_env(connect_fn=_memory_db)
    assert pool.size == 8
    assert pool.checkout_timeout == 2.5


def test_pool_from_env_rejects_bad_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_POOL_SIZE, "many")
    with pytest.raises(ValueError, match=ENV_POOL_SIZE):
        pool_from_env(connect_fn=_memory_db)


def test_pool_from_env_rejects_bad_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_POOL_TIMEOUT, "soon")
    with pytest.raises(ValueError, match=ENV_POOL_TIMEOUT):
        pool_from_env(connect_fn=_memory_db)
