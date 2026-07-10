import os

import pytest

from crynux_server import db
from crynux_server.config import DBConfig
from crynux_server.models import (InferenceTaskState, InferenceTaskStatus,
                                  TaskType)
from crynux_server.task import (DbInferenceTaskStateCache,
                                MemoryInferenceTaskStateCache)

TASK_ID = bytes([1] * 32)


def make_state() -> InferenceTaskState:
    return InferenceTaskState(
        task_id_commitment=TASK_ID,
        timeout=900,
        status=InferenceTaskStatus.Started,
        task_type=TaskType.SD,
        files=["test.png"],
        score=bytes([1] * 8),
        checkpoint="",
    )


@pytest.fixture
async def init_db(tmp_path):
    filename = str(tmp_path / "test.db")
    await db.init(DBConfig.model_validate({"driver": "sqlite", "filename": filename}))
    yield
    await db.close()
    if os.path.exists(filename):
        os.remove(filename)


async def run_cache_roundtrip(cache):
    state = make_state()
    await cache.dump(state)

    assert await cache.has(TASK_ID)
    loaded = await cache.load(TASK_ID)
    assert loaded == state
    assert not loaded.result_uploaded

    state.status = InferenceTaskStatus.Validated
    state.result_uploaded = True
    await cache.dump(state)

    loaded = await cache.load(TASK_ID)
    assert loaded.status == InferenceTaskStatus.Validated
    assert loaded.result_uploaded

    states = await cache.find(status=[InferenceTaskStatus.Validated])
    assert len(states) == 1
    assert states[0].result_uploaded
    assert len(await cache.find(status=[InferenceTaskStatus.Started])) == 0


async def test_memory_state_cache():
    await run_cache_roundtrip(MemoryInferenceTaskStateCache())


async def test_db_state_cache(init_db):
    await run_cache_roundtrip(DbInferenceTaskStateCache())
