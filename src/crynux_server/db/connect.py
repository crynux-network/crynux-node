from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import sqlalchemy as sa
from anyio import fail_after
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (AsyncEngine, AsyncSession,
                                    async_sessionmaker, create_async_engine)

from crynux_server.config import get_config, DBConfig

from .models import Base

__all__ = ["session_scope", "init", "close", "Base", "get_session"]

_local = threading.local()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if (not hasattr(_local, "session")) or (not hasattr(_local, "engine")):
        raise ValueError("db has not been initialized")
    session: async_sessionmaker[AsyncSession] = _local.session
    async with session() as sess:
        try:
            yield sess
        except:
            with fail_after(5, shield=True):
                await sess.rollback()
            raise


session_scope = asynccontextmanager(get_session)


async def init(db: DBConfig | None = None):
    if db is None:        
        db = get_config().db

    if hasattr(_local, "session") or hasattr(_local, "engine"):
        raise ValueError("db has been initialized")

    if not os.path.exists(db.filename):
        dirname = os.path.dirname(db.filename)
        if len(dirname) > 0:
            os.makedirs(dirname, exist_ok=True)

    engine = create_async_engine(
        db.connection,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool
        # echo=True,
    )
    session = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all never alters existing tables, so columns added to
        # existing tables must be created here for old databases
        res = await conn.execute(sa.text("PRAGMA table_info(inference_task_states)"))
        columns = {row[1] for row in res}
        if "result_uploaded" not in columns:
            await conn.execute(
                sa.text(
                    "ALTER TABLE inference_task_states "
                    "ADD COLUMN result_uploaded BOOLEAN NOT NULL DEFAULT 0"
                )
            )

    _local.engine = engine
    _local.session = session


async def close():
    if (not hasattr(_local, "session")) or (not hasattr(_local, "engine")):
        raise ValueError("db has not been initialized")

    engine: AsyncEngine = _local.engine
    await engine.dispose()

    delattr(_local, "engine")
    delattr(_local, "session")
