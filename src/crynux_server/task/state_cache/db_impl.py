import json
from datetime import datetime
from typing import List, Optional

import sqlalchemy as sa

from crynux_server import db
from crynux_server.db import models as db_models
from crynux_server.models import (DownloadTaskState, DownloadTaskStatus,
                                  InferenceTaskState, InferenceTaskStatus,
                                  ModelFileEntry)

from .abc import DownloadTaskStateCache, InferenceTaskStateCache


def _dump_model_files(model_files: List[ModelFileEntry]) -> str:
    return json.dumps([entry.model_dump() for entry in model_files])


def _load_model_files(data: str) -> List[ModelFileEntry]:
    return [ModelFileEntry.model_validate(entry) for entry in json.loads(data)]


def _decode_task_id(task_id_commitment: str) -> bytes:
    raw = task_id_commitment
    if raw.startswith("0x") or raw.startswith("0X"):
        raw = raw[2:]
    return bytes.fromhex(raw)


class DbInferenceTaskStateCache(InferenceTaskStateCache):
    async def load(self, task_id_commitment: bytes) -> InferenceTaskState:
        async with db.session_scope() as sess:
            q = sa.select(db_models.InferenceTaskState).where(
                db_models.InferenceTaskState.task_id_commitment
                == task_id_commitment.hex()
            )
            state = (await sess.scalars(q)).one_or_none()
            if state is not None:
                files = state.files.split(",")
                return InferenceTaskState(
                    task_id_commitment=task_id_commitment,
                    timeout=state.timeout,
                    status=state.status,
                    task_type=state.task_type,
                    files=files,
                    score=state.score,
                    waiting_tx_hash=state.waiting_tx_hash,
                    waiting_tx_method=state.waiting_tx_method,
                    checkpoint=state.checkpoint,
                    result_uploaded=state.result_uploaded,
                    execution_dtype=state.execution_dtype,
                )
            else:
                raise KeyError(
                    f"Inference task state of {task_id_commitment.hex()} not found."
                )

    async def dump(self, task_state: InferenceTaskState):
        async with db.session_scope() as sess:
            task_id_commitment = task_state.task_id_commitment

            q = sa.select(db_models.InferenceTaskState).where(
                db_models.InferenceTaskState.task_id_commitment
                == task_id_commitment.hex()
            )
            state = (await sess.scalars(q)).one_or_none()
            if state is None:
                state = db_models.InferenceTaskState(
                    task_id_commitment=task_id_commitment.hex(),
                    timeout=task_state.timeout,
                    status=task_state.status,
                    task_type=task_state.task_type,
                    files=",".join(task_state.files),
                    score=task_state.score,
                    waiting_tx_hash=task_state.waiting_tx_hash,
                    waiting_tx_method=task_state.waiting_tx_method,
                    checkpoint=task_state.checkpoint,
                    result_uploaded=task_state.result_uploaded,
                    execution_dtype=task_state.execution_dtype,
                )
                sess.add(state)
            else:
                state.timeout = task_state.timeout
                state.status = task_state.status
                state.task_type = task_state.task_type
                state.files = ",".join(task_state.files)
                state.score = task_state.score
                state.waiting_tx_hash = task_state.waiting_tx_hash
                state.waiting_tx_method = task_state.waiting_tx_method
                state.checkpoint = task_state.checkpoint
                state.result_uploaded = task_state.result_uploaded
                state.execution_dtype = task_state.execution_dtype
            await sess.commit()

    async def has(self, task_id_commitment: bytes) -> bool:
        async with db.session_scope() as sess:
            q = sa.select(db_models.InferenceTaskState).where(
                db_models.InferenceTaskState.task_id_commitment
                == task_id_commitment.hex()
            )
            state = (await sess.scalars(q)).one_or_none()
            return state is not None

    async def find(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        status: Optional[List[InferenceTaskStatus]] = None,
    ) -> List[InferenceTaskState]:
        async with db.session_scope() as sess:
            q = sa.select(db_models.InferenceTaskState)
            if start is not None:
                q = q.where(db_models.InferenceTaskState.updated_at >= start)
            if end is not None:
                q = q.where(db_models.InferenceTaskState.updated_at < end)
            if status is not None:
                q = q.where(db_models.InferenceTaskState.status.in_(status))

            states = (await sess.execute(q)).scalars().all()
            return [
                InferenceTaskState(
                    task_id_commitment=_decode_task_id(state.task_id_commitment),
                    timeout=state.timeout,
                    status=state.status,
                    task_type=state.task_type,
                    files=state.files.split(","),
                    score=state.score,
                    waiting_tx_hash=state.waiting_tx_hash,
                    waiting_tx_method=state.waiting_tx_method,
                    checkpoint=state.checkpoint,
                    result_uploaded=state.result_uploaded,
                    execution_dtype=state.execution_dtype,
                )
                for state in states
            ]

    async def get_latest_created(self) -> Optional[InferenceTaskState]:
        async with db.session_scope() as sess:
            q = (
                sa.select(db_models.InferenceTaskState)
                .order_by(db_models.InferenceTaskState.id.desc())
                .limit(1)
            )
            state = (await sess.scalars(q)).one_or_none()
            if state is None:
                return None
            return InferenceTaskState(
                task_id_commitment=_decode_task_id(state.task_id_commitment),
                timeout=state.timeout,
                status=state.status,
                task_type=state.task_type,
                files=state.files.split(","),
                score=state.score,
                waiting_tx_hash=state.waiting_tx_hash,
                waiting_tx_method=state.waiting_tx_method,
                checkpoint=state.checkpoint,
                result_uploaded=state.result_uploaded,
                execution_dtype=state.execution_dtype,
            )


class DbDownloadTaskStateCache(DownloadTaskStateCache):
    async def load(self, task_id: str) -> DownloadTaskState:
        async with db.session_scope() as sess:
            q = sa.select(db_models.DownloadTaskState).where(
                db_models.DownloadTaskState.task_id == task_id
            )
            state = (await sess.scalars(q)).one_or_none()
            if state is not None:
                return DownloadTaskState(
                    task_id=task_id,
                    task_type=state.task_type,
                    model_files=_load_model_files(state.model_files),
                    status=state.status,
                )
            else:
                raise KeyError(f"Download task state of {task_id} not found.")

    async def dump(self, task_state: DownloadTaskState):
        async with db.session_scope() as sess:
            q = sa.select(db_models.DownloadTaskState).where(
                db_models.DownloadTaskState.task_id == task_state.task_id
            )
            state = (await sess.scalars(q)).one_or_none()

            if state is None:
                state = db_models.DownloadTaskState(
                    task_id=task_state.task_id,
                    task_type=task_state.task_type,
                    model_files=_dump_model_files(task_state.model_files),
                    status=task_state.status,
                )
                sess.add(state)
            else:
                state.task_id = task_state.task_id
                state.task_type = task_state.task_type
                state.model_files = _dump_model_files(task_state.model_files)
                state.status = task_state.status
            await sess.commit()

    async def has(self, task_id: str) -> bool:
        async with db.session_scope() as sess:
            q = sa.select(db_models.DownloadTaskState).where(
                db_models.DownloadTaskState.task_id == task_id
            )
            state = (await sess.scalars(q)).one_or_none()
            return state is not None

    async def find(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        status: List[DownloadTaskStatus] | None = None,
    ) -> List[DownloadTaskState]:
        async with db.session_scope() as sess:
            q = sa.select(db_models.DownloadTaskState)
            if start is not None:
                q = q.where(db_models.DownloadTaskState.updated_at >= start)
            if end is not None:
                q = q.where(db_models.DownloadTaskState.updated_at <= end)
            if status is not None:
                q = q.where(db_models.DownloadTaskState.status.in_(status))
            states = (await sess.scalars(q)).all()
            return [
                DownloadTaskState(
                    task_id=state.task_id,
                    task_type=state.task_type,
                    model_files=_load_model_files(state.model_files),
                    status=state.status,
                )
                for state in states
            ]
