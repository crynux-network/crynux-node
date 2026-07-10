import logging
from contextlib import asynccontextmanager
from typing import Optional

from anyio import fail_after

from crynux_server import models
from crynux_server.contracts import Contracts, get_contracts
from crynux_server.download_model_cache import (DownloadModelCache,
                                                get_download_model_cache)
from crynux_server.relay import Relay, get_relay

from .state_cache import (DownloadTaskStateCache,
                          get_download_task_state_cache)
from .utils import run_download_task

_logger = logging.getLogger(__name__)


class DownloadTaskRunner(object):
    def __init__(
        self,
        task_id: str,
        state: models.DownloadTaskState,
        state_cache: Optional[DownloadTaskStateCache] = None,
        contracts: Optional[Contracts] = None,
        relay: Optional[Relay] = None,
        download_model_cache: Optional[DownloadModelCache] = None,
    ):
        self.task_id = task_id
        if state_cache is None:
            state_cache = get_download_task_state_cache()
        self.state_cache = state_cache
        if contracts is None:
            contracts = get_contracts()
        self.contracts = contracts
        if relay is None:
            relay = get_relay()
        self.relay = relay
        if download_model_cache is None:
            download_model_cache = get_download_model_cache()
        self.download_model_cache = download_model_cache

        self._state: models.DownloadTaskState = state

    @asynccontextmanager
    async def state_context(self):
        try:
            yield
        finally:
            with fail_after(10, shield=True):
                await self.state_cache.dump(task_state=self._state)

    async def run(self):
        if await self.state_cache.has(self.task_id):
            self._state = await self.state_cache.load(self.task_id)
        else:
            await self.state_cache.dump(self._state)

        if self._state.status == models.DownloadTaskStatus.Success:
            return

        model = models.ModelConfig.from_model_id(self._state.model_id)
        if self._state.status == models.DownloadTaskStatus.Started:
            _logger.info(f"start downloading model {self._state.model_id}")
            await run_download_task(
                task_id=self.task_id, task_type=self._state.task_type, model=model
            )
            async with self.state_context():
                self._state.status = models.DownloadTaskStatus.Executed
            _logger.info(f"Download model {self._state.model_id} successfully")

        if self._state.status == models.DownloadTaskStatus.Executed:
            await self.relay.node_report_model_downloaded(self._state.model_id)
            _logger.info(f"report model {self._state.model_id} is downloaded")
            async with self.state_context():
                self._state.status = models.DownloadTaskStatus.Success

            await self.download_model_cache.save(
                models.DownloadedModel(task_type=self._state.task_type, model=model)
            )

    async def mark_failed(self):
        async with self.state_context():
            self._state.status = models.DownloadTaskStatus.Failed
