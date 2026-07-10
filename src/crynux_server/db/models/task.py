from typing import Optional, get_args

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from crynux_server.models import (
    InferenceTaskStatus,
    TaskType,
    DownloadTaskStatus,
)

from .base import Base, BaseMixin


class InferenceTaskState(Base, BaseMixin):
    __tablename__ = "inference_task_states"

    task_id_commitment: Mapped[str] = mapped_column(
        sa.String(length=64), nullable=False, index=True
    )
    timeout: Mapped[int] = mapped_column(sa.Integer, nullable=False, index=False)
    status: Mapped[InferenceTaskStatus] = mapped_column(
        sa.Enum(InferenceTaskStatus), nullable=False, index=True
    )
    task_type: Mapped[TaskType] = mapped_column(
        sa.Enum(TaskType), nullable=False, index=True
    )
    files: Mapped[str] = mapped_column(sa.Text, nullable=False, index=False)
    score: Mapped[bytes] = mapped_column(
        sa.LargeBinary, nullable=False, index=False, default=b""
    )
    waiting_tx_hash: Mapped[bytes] = mapped_column(
        sa.BINARY, nullable=False, index=False, default=b""
    )
    waiting_tx_method: Mapped[str] = mapped_column(
        nullable=False, index=False, default=""
    )
    checkpoint: Mapped[Optional[str]] = mapped_column(
        nullable=True, index=False, default=""
    )
    result_uploaded: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, index=False, default=False
    )


class DownloadTaskState(Base, BaseMixin):
    __tablename__ = "download_task_states"

    task_id: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    task_type: Mapped[TaskType] = mapped_column(
        sa.Enum(TaskType), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(sa.Text, nullable=False, index=False)
    status: Mapped[DownloadTaskStatus] = mapped_column(
        sa.Enum(DownloadTaskStatus), nullable=False, index=True
    )
