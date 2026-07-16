import time
from types import SimpleNamespace

import pytest
from anyio import sleep

from crynux_server.models import (DownloadTaskInput, ErrorResult,
                                  InferenceTaskInput, ModelConfig,
                                  SuccessResult, TaskInput, TaskResult)
from crynux_server.worker_manager import (TaskCancelled, TaskDownloadError,
                                          TaskExecutionError, TaskInvalid,
                                          WorkerManager)
from crynux_server.worker_manager import manager as manager_module

TASK_ID = "01" * 32


def make_task_input(task_id: str = TASK_ID) -> TaskInput:
    return TaskInput(
        task=InferenceTaskInput(
            task_name="inference",
            task_type=0,
            task_id=task_id,
            models=[],
            task_args="{}",
            output_dir="output",
        )
    )


def make_task_result(status: str, traceback: str = "") -> TaskResult:
    if status == "success":
        result = SuccessResult(status="success")
    else:
        result = ErrorResult(status="error", traceback=traceback)
    return TaskResult(
        task_name="inference", task_id_commitment=TASK_ID, result=result
    )


def make_worker_manager(monkeypatch, role="inference"):
    wm = WorkerManager(role=role, config=SimpleNamespace())
    wm.restart_calls = 0

    def fake_start():
        pass

    def fake_stop():
        wm.restart_calls += 1

    monkeypatch.setattr(wm, "_start_worker_process", fake_start)
    monkeypatch.setattr(wm, "_stop_worker_process", fake_stop)
    return wm


@pytest.fixture
def worker_manager(monkeypatch):
    monkeypatch.setattr(manager_module, "WATCHDOG_CHECK_INTERVAL", 0.05)
    wm = make_worker_manager(monkeypatch)
    yield wm
    if wm._watchdog_task is not None:
        wm._watchdog_task.cancel()


@pytest.fixture
def download_worker_manager(monkeypatch):
    monkeypatch.setattr(manager_module, "WATCHDOG_CHECK_INTERVAL", 0.05)
    wm = make_worker_manager(monkeypatch, role="download")
    yield wm
    if wm._watchdog_task is not None:
        wm._watchdog_task.cancel()


async def dispatch_task(wm: WorkerManager):
    worker_id = await wm.connect("1.0.0")
    fut = await wm.send_task(make_task_input())
    await wm.get_task(worker_id)
    return worker_id, fut


async def test_execution_error_result_restarts_worker_immediately(worker_manager):
    worker_id, fut = await dispatch_task(worker_manager)

    await worker_manager.report_task_result(
        worker_id, make_task_result("error", "CUDA error: device-side assert triggered")
    )
    with pytest.raises(TaskExecutionError):
        await fut.get()

    # The restart runs as an independent asyncio task
    await sleep(0.1)
    assert worker_manager.restart_calls == 1


async def test_task_invalid_result_does_not_restart_worker(worker_manager):
    worker_id, fut = await dispatch_task(worker_manager)

    await worker_manager.report_task_result(
        worker_id, make_task_result("error", "Task args invalid")
    )
    with pytest.raises(TaskInvalid):
        await fut.get()

    await sleep(0.1)
    assert worker_manager.restart_calls == 0


async def test_success_result_does_not_restart_worker(worker_manager):
    worker_id, fut = await dispatch_task(worker_manager)

    await worker_manager.report_task_result(worker_id, make_task_result("success"))
    await fut.get()

    await sleep(0.1)
    assert worker_manager.restart_calls == 0


async def test_watchdog_restarts_worker_on_missed_deadline(worker_manager):
    worker_id = await worker_manager.connect("1.0.0")
    fut = await worker_manager.send_task(
        make_task_input(), deadline=time.time() + 0.1
    )
    await worker_manager.get_task(worker_id)

    await sleep(0.5)
    assert worker_manager.restart_calls == 1
    # The restart cancels the in-flight task future
    with pytest.raises(TaskCancelled):
        await fut.get()


async def test_result_before_deadline_prevents_watchdog_restart(worker_manager):
    worker_id = await worker_manager.connect("1.0.0")
    fut = await worker_manager.send_task(
        make_task_input(), deadline=time.time() + 0.2
    )
    await worker_manager.get_task(worker_id)

    await worker_manager.report_task_result(worker_id, make_task_result("success"))
    await fut.get()

    await sleep(0.5)
    assert worker_manager.restart_calls == 0


async def test_locally_cancelled_future_still_restarts_at_deadline(worker_manager):
    worker_id = await worker_manager.connect("1.0.0")
    fut = await worker_manager.send_task(
        make_task_input(), deadline=time.time() + 0.5
    )
    await worker_manager.get_task(worker_id)

    # A future cancelled from the caller side is not a worker-reported
    # result, so the watchdog entry stays
    fut.cancel()
    with pytest.raises(TaskCancelled):
        await fut.get()

    await sleep(0.2)
    assert worker_manager.restart_calls == 0

    await sleep(0.8)
    assert worker_manager.restart_calls == 1


async def test_model_not_downloaded_result_does_not_restart_worker(worker_manager):
    worker_id, fut = await dispatch_task(worker_manager)

    await worker_manager.report_task_result(
        worker_id,
        make_task_result("error", "sd_task.ModelNotDownloaded: Task model not downloaded"),
    )
    # The protocol path still sees an execution error
    with pytest.raises(TaskExecutionError):
        await fut.get()

    await sleep(0.1)
    assert worker_manager.restart_calls == 0


async def test_download_manager_never_restarts_on_error_result(
    download_worker_manager,
):
    worker_id = await download_worker_manager.connect("1.0.0")
    fut = await download_worker_manager.send_task(
        TaskInput(
            task=DownloadTaskInput(
                task_name="download",
                task_type=0,
                task_id=TASK_ID,
                model=ModelConfig(id="crynux-network/stable-diffusion-v1-5", type="base"),
            )
        )
    )
    await download_worker_manager.get_task(worker_id)

    result = TaskResult(
        task_name="download",
        task_id_commitment=TASK_ID,
        result=ErrorResult(status="error", traceback="network error"),
    )
    await download_worker_manager.report_task_result(worker_id, result)
    with pytest.raises(TaskDownloadError):
        await fut.get()

    await sleep(0.1)
    assert download_worker_manager.restart_calls == 0


async def test_download_manager_ignores_deadline(download_worker_manager):
    worker_id = await download_worker_manager.connect("1.0.0")
    fut = await download_worker_manager.send_task(
        make_task_input(), deadline=time.time() + 0.1
    )
    await download_worker_manager.get_task(worker_id)

    await sleep(0.5)
    # No watchdog on the download manager: no restart, the task stays open
    assert download_worker_manager.restart_calls == 0
    assert not fut.done()


async def test_restart_clears_watchdog_entries(worker_manager):
    worker_id = await worker_manager.connect("1.0.0")
    fut = await worker_manager.send_task(
        make_task_input(), deadline=time.time() + 0.1
    )
    await worker_manager.get_task(worker_id)

    await sleep(0.5)
    # A single restart, not one per watchdog tick after the deadline
    assert worker_manager.restart_calls == 1
    with pytest.raises(TaskCancelled):
        await fut.get()
