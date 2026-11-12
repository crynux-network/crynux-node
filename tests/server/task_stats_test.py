from types import SimpleNamespace
from unittest.mock import patch

import pytest

from crynux_server import models
from crynux_server.node_manager.state_cache import (
    ManagerStateCache,
    MemoryNodeStateCache,
    MemoryTxStateCache,
)
from crynux_server.server.v1.task import get_task_stats
from crynux_server.task import CurrentTaskView, MemoryInferenceTaskStateCache

TASK_ID = bytes([9] * 32)
ORPHAN_ID = bytes([8] * 32)


@pytest.fixture
async def running_state_cache():
    cache = ManagerStateCache(
        node_state_cache_cls=MemoryNodeStateCache,
        tx_state_cache_cls=MemoryTxStateCache,
    )
    await cache.set_node_state(models.NodeStatus.Running)
    return cache


@pytest.fixture
async def stopped_state_cache():
    cache = ManagerStateCache(
        node_state_cache_cls=MemoryNodeStateCache,
        tx_state_cache_cls=MemoryTxStateCache,
    )
    await cache.set_node_state(models.NodeStatus.Stopped)
    return cache


@pytest.fixture
def task_cache():
    return MemoryInferenceTaskStateCache()


def _patch_task_view(synced: bool, task_id=None):
    system = SimpleNamespace(
        get_current_task_view=lambda: CurrentTaskView(synced=synced, task_id=task_id)
    )
    return patch("crynux_server.server.v1.task.get_task_system", return_value=system)


async def test_task_stats_synced_empty_with_orphan_is_none(
    running_state_cache, task_cache
):
    await task_cache.dump(
        models.InferenceTaskState(
            task_id_commitment=ORPHAN_ID,
            timeout=0,
            status=models.InferenceTaskStatus.Started,
            task_type=models.TaskType.SD,
        )
    )
    with _patch_task_view(synced=True, task_id=None):
        stats = await get_task_stats(
            task_state_cache=task_cache, state_cache=running_state_cache
        )
    assert stats.status == "none"


async def test_task_stats_synced_tracked_started(running_state_cache, task_cache):
    await task_cache.dump(
        models.InferenceTaskState(
            task_id_commitment=TASK_ID,
            timeout=0,
            status=models.InferenceTaskStatus.Started,
            task_type=models.TaskType.SD,
        )
    )
    with _patch_task_view(synced=True, task_id=TASK_ID):
        stats = await get_task_stats(
            task_state_cache=task_cache, state_cache=running_state_cache
        )
    assert stats.status == "Started"


async def test_task_stats_synced_tracked_score_ready(running_state_cache, task_cache):
    await task_cache.dump(
        models.InferenceTaskState(
            task_id_commitment=TASK_ID,
            timeout=0,
            status=models.InferenceTaskStatus.ScoreReady,
            task_type=models.TaskType.SD,
        )
    )
    with _patch_task_view(synced=True, task_id=TASK_ID):
        stats = await get_task_stats(
            task_state_cache=task_cache, state_cache=running_state_cache
        )
    assert stats.status == "ScoreReady"


async def test_task_stats_synced_tracked_error_reported(
    running_state_cache, task_cache
):
    await task_cache.dump(
        models.InferenceTaskState(
            task_id_commitment=TASK_ID,
            timeout=0,
            status=models.InferenceTaskStatus.ErrorReported,
            task_type=models.TaskType.SD,
        )
    )
    with _patch_task_view(synced=True, task_id=TASK_ID):
        stats = await get_task_stats(
            task_state_cache=task_cache, state_cache=running_state_cache
        )
    assert stats.status == "ErrorReported"


async def test_task_stats_synced_tracked_without_local_state_is_started(
    running_state_cache, task_cache
):
    with _patch_task_view(synced=True, task_id=TASK_ID):
        stats = await get_task_stats(
            task_state_cache=task_cache, state_cache=running_state_cache
        )
    assert stats.status == "Started"


async def test_task_stats_not_synced_is_unavailable(running_state_cache, task_cache):
    await task_cache.dump(
        models.InferenceTaskState(
            task_id_commitment=TASK_ID,
            timeout=0,
            status=models.InferenceTaskStatus.Started,
            task_type=models.TaskType.SD,
        )
    )
    with _patch_task_view(synced=False, task_id=TASK_ID):
        stats = await get_task_stats(
            task_state_cache=task_cache, state_cache=running_state_cache
        )
    assert stats.status == "unavailable"


async def test_task_stats_task_system_unset_is_unavailable(
    running_state_cache, task_cache
):
    with patch(
        "crynux_server.server.v1.task.get_task_system",
        side_effect=AssertionError("TaskSystem has not been set."),
    ):
        stats = await get_task_stats(
            task_state_cache=task_cache, state_cache=running_state_cache
        )
    assert stats.status == "unavailable"


async def test_task_stats_node_stopped_is_stopped(stopped_state_cache, task_cache):
    with _patch_task_view(synced=True, task_id=TASK_ID):
        stats = await get_task_stats(
            task_state_cache=task_cache, state_cache=stopped_state_cache
        )
    assert stats.status == "stopped"
