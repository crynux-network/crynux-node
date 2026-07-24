from types import SimpleNamespace

from crynux_server.server.v1 import settings as settings_module
from crynux_server.server.v1.settings import (
    SetSettingsInput,
    flush_task_error_reports,
    get_settings,
    set_settings,
)
from crynux_server.task.error_report import FlushResult


class FakeStore:
    async def count(self):
        return 3


class FakeReporter:
    def __init__(self):
        self.store = FakeStore()
        self.notified = 0

    async def notify(self):
        self.notified += 1

    async def flush(self):
        return FlushResult(reported=2, remaining=1)


async def test_settings_exposes_diagnostic_configuration(monkeypatch):
    reporter = FakeReporter()
    monkeypatch.setattr(settings_module, "get_staking_amount", lambda: 100)
    monkeypatch.setattr(
        settings_module,
        "get_config",
        lambda: SimpleNamespace(task_error_report=SimpleNamespace(automatic=False)),
    )
    monkeypatch.setattr(
        settings_module,
        "get_task_system",
        lambda: SimpleNamespace(error_reporter=reporter),
    )

    response = await get_settings()

    assert response.task_error_report_automatic is False
    assert response.pending_task_error_reports == 3


async def test_settings_updates_automatic_reporting_and_wakes_reporter(
    monkeypatch,
):
    reporter = FakeReporter()
    values = []
    monkeypatch.setattr(
        settings_module,
        "set_task_error_report_automatic",
        values.append,
    )
    monkeypatch.setattr(
        settings_module,
        "get_task_system",
        lambda: SimpleNamespace(error_reporter=reporter),
    )

    await set_settings(SetSettingsInput(task_error_report_automatic=True))

    assert values == [True]
    assert reporter.notified == 1


async def test_manual_flush_returns_reported_and_remaining(monkeypatch):
    reporter = FakeReporter()
    monkeypatch.setattr(
        settings_module,
        "get_task_system",
        lambda: SimpleNamespace(error_reporter=reporter),
    )

    response = await flush_task_error_reports()

    assert response.reported == 2
    assert response.remaining == 1
