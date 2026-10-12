"""Resolved SQLite wait budgets reach distinct connections and scoped kits."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy.exc import OperationalError

from m3 import MCPTestKit
from m3._default_store import (
    install_default_store_factory,
    make_default_store,
    restore_default_store_factory,
)
from m3._results_settings import sqlite_busy_timeout_ms
from m3.async_api import AsyncMCPTestKit
from m3.configuration import Config, load_config
from m3.storage import SQLiteExecutionStore
from m3.types import ResultsSettings

pytest_plugins = ["pytester"]


@pytest.mark.parametrize(
    "seconds, expected", [(None, 5000), (30, 30000), (0.0001, 1), (0.0301, 31)]
)
def test_default_and_millisecond_precision(
    seconds: float | None, expected: int
) -> None:
    settings = (
        ResultsSettings()
        if seconds is None
        else ResultsSettings(busy_timeout_seconds=seconds)
    )
    assert sqlite_busy_timeout_ms(settings) == expected


def test_timeout_must_fit_sqlites_positive_millisecond_counter() -> None:
    with pytest.raises(ValueError, match="SQLite's millisecond range"):
        sqlite_busy_timeout_ms(ResultsSettings(busy_timeout_seconds=2**31))


def test_scoped_factory_keeps_zero_argument_compatibility_and_nested_restore() -> None:
    first_config = Config(results=ResultsSettings(busy_timeout_seconds=30))
    second_config = Config(results=ResultsSettings(busy_timeout_seconds=10))
    legacy = object()
    outer = install_default_store_factory(
        lambda: legacy, configured_factory=lambda config: config
    )
    inner = install_default_store_factory(lambda: "nested")
    try:
        assert make_default_store(first_config) == "nested"
        restore_default_store_factory(outer)
        assert make_default_store(second_config) == "nested"
        restore_default_store_factory(inner)
        assert make_default_store(first_config) is first_config
        assert make_default_store(second_config) is second_config
        assert make_default_store() is legacy
    finally:
        restore_default_store_factory(outer)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_scoped_kits_use_effective_results_config_and_explicit_stores_win(
    tmp_path: Path,
    asynchronous: bool,
) -> None:
    path = tmp_path.resolve() / "scoped.sqlite"
    (tmp_path / "pyproject.toml").write_text(
        "[tool.m3.results]\nbusy_timeout_seconds = 30\n", encoding="utf-8"
    )
    inherited = load_config(env={}, cwd=tmp_path)
    overridden = load_config(
        {"results": {"busy_timeout_seconds": 0.125}}, env={}, cwd=tmp_path
    )
    created: list[SQLiteExecutionStore] = []

    def configured(configuration: Config) -> SQLiteExecutionStore:
        store = SQLiteExecutionStore(
            path, busy_timeout_ms=sqlite_busy_timeout_ms(configuration.results)
        )
        created.append(store)
        return store

    token = install_default_store_factory(
        lambda: configured(inherited), configured_factory=configured
    )
    explicit = SQLiteExecutionStore(
        tmp_path.resolve() / "explicit.sqlite", busy_timeout_ms=70
    )
    try:
        if asynchronous:

            async def exercise() -> None:
                for config, expected in (
                    (inherited, 30000),
                    (overridden, 125),
                    (Config(), 5000),
                ):
                    kit = AsyncMCPTestKit(
                        config=config, env={}, cwd=tmp_path, embedded_worker=False
                    )
                    assert created[-1].busy_timeout_ms == expected
                    await kit.aclose()
                kit = AsyncMCPTestKit(
                    config=overridden,
                    store=explicit,
                    env={},
                    cwd=tmp_path,
                    embedded_worker=False,
                )
                assert explicit.busy_timeout_ms == 70
                await kit.aclose()

            asyncio.run(exercise())
        else:
            for config, expected in (
                (inherited, 30000),
                (overridden, 125),
                (Config(), 5000),
            ):
                kit = MCPTestKit(
                    config=config, env={}, cwd=tmp_path, embedded_worker=False
                )
                assert kit.store is created[-1]
                assert created[-1].busy_timeout_ms == expected
                kit.close()
            kit = MCPTestKit(
                config=overridden,
                store=explicit,
                env={},
                cwd=tmp_path,
                embedded_worker=False,
            )
            assert kit.store is explicit
            assert explicit.busy_timeout_ms == 70
            kit.close()
    finally:
        explicit.close()
        restore_default_store_factory(token)


def test_configured_wait_budget_reaches_multiple_connections_and_real_contention(
    tmp_path: Path,
) -> None:
    database = tmp_path.resolve() / "contention.sqlite"
    timeout = sqlite_busy_timeout_ms(ResultsSettings(busy_timeout_seconds=0.03))
    store = SQLiteExecutionStore(database, busy_timeout_ms=timeout)
    with store._connect() as first, store._connect() as second:
        assert first.execute("PRAGMA busy_timeout").fetchone()[0] == timeout
        assert second.execute("PRAGMA busy_timeout").fetchone()[0] == timeout
    holder = sqlite3.connect(database, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    finished = Event()

    def blocked_write() -> float:
        started = time.monotonic()
        try:
            with pytest.raises(OperationalError, match="database is locked"):
                store.ensure_suite("blocked-suite")
            return time.monotonic() - started
        finally:
            finished.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(blocked_write)
            try:
                assert finished.wait(2), (
                    "configured short budget did not end contention"
                )
                assert result.result(timeout=2) >= 0.025
            finally:
                holder.execute("ROLLBACK")
        store.ensure_suite("after-release")
    finally:
        holder.close()
        store.close()


def test_successfully_started_transaction_still_rolls_back(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path.resolve() / "rollback.sqlite")
    try:
        with store._connect() as connection:
            store._begin(connection, immediate=True)
            assert connection.in_transaction
            connection.execute(
                "INSERT INTO v2_projects(id,project_name,created_at,updated_at) VALUES(?,?,?,?)",
                ("00000000-0000-0000-0000-000000000001", "uncommitted", "now", "now"),
            )
            store._rollback(connection)
            assert not connection.in_transaction
        assert store.get_project("00000000-0000-0000-0000-000000000001") is None
    finally:
        store.close()


def test_real_workers_use_frozen_results_config_for_manifest_kits_and_export(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[2] / "src"))
    pytester.makepyprojecttoml("[tool.m3.results]\nbusy_timeout_seconds = 0.3\n")
    pytester.makeconftest("""
import os
from pathlib import Path
import m3.storage
OriginalStore = m3.storage.SQLiteExecutionStore
class RecordedStore(OriginalStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
        with Path("constructed-" + worker).open("a", encoding="utf-8") as output:
            output.write(str(self.busy_timeout_ms) + "\\n")
m3.storage.SQLiteExecutionStore = RecordedStore
""")
    pytester.makepyfile("""
from pathlib import Path
import pytest
from m3 import MCPTestKit
pytestmark = pytest.mark.m3(suite_name="configured-timeout")
@pytest.mark.parametrize("trial", range(4))
def test_settings(trial, request):
    Path("pyproject.toml").unlink(missing_ok=True)
    assert request.config._m3_manifest_store.busy_timeout_ms == 300
    with MCPTestKit(embedded_worker=False) as kit:
        assert kit.config.results.busy_timeout_seconds == 0.3
        assert kit.store.busy_timeout_ms == 300
    with MCPTestKit({"results": {"busy_timeout_seconds": 0.09}}, embedded_worker=False) as kit:
        assert kit.store.busy_timeout_ms == 90
""")
    result = pytester.runpytest_subprocess(
        "-p",
        "m3.pytest_plugin",
        "--results-db",
        str(pytester.path.resolve() / "results.sqlite"),
        "-n",
        "2",
        "-q",
        timeout=30,
    )
    result.assert_outcomes(passed=4)
    controller = (pytester.path / "constructed-controller").read_text().splitlines()
    assert controller[0] == controller[-1] == "300"
    assert len(controller) >= 2
    for worker in ("gw0", "gw1"):
        constructed = (
            (pytester.path / ("constructed-" + worker)).read_text().splitlines()
        )
        assert constructed[0] == "300"
        assert "90" in constructed
