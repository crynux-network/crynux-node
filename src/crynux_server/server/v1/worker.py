import logging

from anyio import create_task_group, fail_after
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from crynux_server.models import TaskResult
from crynux_server.worker_manager import (TaskDownloadError,
                                          TaskExecutionError, TaskInvalid,
                                          WorkerManager, is_task_invalid)

from ..depends import WorkerManagerDep

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/worker")


async def task_producer(
    worker_id: int, websocket: WebSocket, worker_manager: WorkerManager
):
    while True:
        try:
            with fail_after(1):
                task_input, task_future = await worker_manager.get_task(worker_id)
        except TimeoutError:
            try:
                await websocket.send_text("")
                continue
            except WebSocketDisconnect:
                raise
        # A done or cancelled future means the task runner has stopped
        # waiting for this task, so it must not be sent to the worker
        if task_future.done():
            _logger.info(
                f"Skip sending task {task_input.task.task_id} to worker "
                f"{worker_id} because the task has been cancelled or done"
            )
            continue
        await websocket.send_json(task_input.model_dump())


async def result_consumer(
    worker_id: int, websocket: WebSocket, worker_manager: WorkerManager
):
    while True:
        raw_result = await websocket.receive_json()
        result = TaskResult.model_validate(raw_result)
        with worker_manager.task_future(worker_id, result.task_id_commitment) as fut:
            if fut.cancelled():
                _logger.info(f"Task {result.task_id_commitment} has been cancelled before")
            elif fut.done():
                _logger.info(f"Task {result.task_id_commitment} has been done before")
            else:
                if result.result.status == "success":
                    fut.set_result(None)
                elif result.result.status == "error":
                    err_msg = result.result.traceback
                    if result.task_name == "inference":
                        if is_task_invalid(err_msg):
                            exc = TaskInvalid(err_msg)
                        else:
                            exc = TaskExecutionError(err_msg)
                        fut.set_error(exc)
                    elif result.task_name == "download":
                        exc = TaskDownloadError(err_msg)
                        fut.set_error(exc)


@router.websocket("/")
async def worker(websocket: WebSocket, worker_manager: WorkerManagerDep):
    await websocket.accept()
    version_msg = await websocket.receive_json()
    version = version_msg["version"]
    worker_id = await worker_manager.connect(version)
    await websocket.send_json({"worker_id": worker_id})
    _logger.info(f"worker {worker_id} connects")
    try:
        async with create_task_group() as tg:
            tg.start_soon(task_producer, worker_id, websocket, worker_manager)
            tg.start_soon(result_consumer, worker_id, websocket, worker_manager)
    except WebSocketDisconnect:
        _logger.error(f"worker {worker_id} disconnects")
        pass
    except Exception as e:
        _logger.error(f"worker {worker_id} unexpected error")
        _logger.exception(e)
        raise
    finally:
        await worker_manager.disconnect(worker_id)
