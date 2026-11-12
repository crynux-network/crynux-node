import os
import sqlite3

import pytest

from crynux_server import db
from crynux_server.config import DBConfig
from crynux_server.models import (InferenceTaskState, InferenceTaskStatus,
                                  TaskType)
from crynux_server.task import (DbInferenceTaskStateCache,
                                MemoryInferenceTaskStateCache)

TASK_ID = bytes([1] * 32)
TASK_ID_2 = bytes([2] * 32)


def make_state(task_id: bytes = TASK_ID) -> InferenceTaskState:
    return InferenceTaskState(
        task_id_commitment=task_id,
        timeout=900,
        status=InferenceTaskStatus.Started,
        task_type=TaskType.SD,
        files=["test.png"],
        score=bytes([1] * 8),
        checkpoint="",
        execution_dtype="bfloat16",
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
    assert loaded.execution_dtype == "bfloat16"
    assert not loaded.result_uploaded

    state.status = InferenceTaskStatus.Validated
    state.result_uploaded = True
    await cache.dump(state)

    loaded = await cache.load(TASK_ID)
    assert loaded.status == InferenceTaskStatus.Validated
    assert loaded.result_uploaded

    states = await cache.find(status=[InferenceTaskStatus.Validated])
    assert len(states) == 1
    assert states[0].task_id_commitment == TASK_ID
    assert states[0].result_uploaded
    assert len(await cache.find(status=[InferenceTaskStatus.Started])) == 0


async def test_memory_state_cache():
    await run_cache_roundtrip(MemoryInferenceTaskStateCache())


async def test_db_state_cache(init_db):
    await run_cache_roundtrip(DbInferenceTaskStateCache())


async def run_latest_created(cache):
    assert await cache.get_latest_created() is None

    first = make_state(TASK_ID)
    await cache.dump(first)
    latest = await cache.get_latest_created()
    assert latest is not None
    assert latest.task_id_commitment == TASK_ID

    second = make_state(TASK_ID_2)
    await cache.dump(second)
    latest = await cache.get_latest_created()
    assert latest is not None
    assert latest.task_id_commitment == TASK_ID_2

    first.status = InferenceTaskStatus.EndSuccess
    await cache.dump(first)
    latest = await cache.get_latest_created()
    assert latest is not None
    assert latest.task_id_commitment == TASK_ID_2


async def test_memory_get_latest_created():
    await run_latest_created(MemoryInferenceTaskStateCache())


async def test_db_get_latest_created(init_db):
    await run_latest_created(DbInferenceTaskStateCache())


async def test_db_init_adds_execution_dtype_to_existing_state_table(tmp_path):
    filename = str(tmp_path / "legacy.db")
    with sqlite3.connect(filename) as conn:
        conn.execute(
            """
            CREATE TABLE inference_task_states (
                id INTEGER PRIMARY KEY,
                task_id_commitment VARCHAR(64) NOT NULL,
                timeout INTEGER NOT NULL,
                status VARCHAR(32) NOT NULL,
                task_type VARCHAR(32) NOT NULL,
                files TEXT NOT NULL,
                score BLOB NOT NULL,
                waiting_tx_hash BLOB NOT NULL,
                waiting_tx_method VARCHAR NOT NULL,
                checkpoint VARCHAR,
                result_uploaded BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

    await db.init(DBConfig.model_validate({"driver": "sqlite", "filename": filename}))
    try:
        with sqlite3.connect(filename) as conn:
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(inference_task_states)"
                ).fetchall()
            }
        assert "execution_dtype" in columns
    finally:
        await db.close()
