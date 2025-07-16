import logging
from contextlib import asynccontextmanager

from anyio import create_task_group, sleep
from fastapi import FastAPI

from .system import update_system_info
from .account import update_account_info

_logger = logging.getLogger(__name__)


class Lifespan(object):
    def __init__(
        self,
        hf_model_dir: str,
        external_model_dir: str,
        log_dir: str,
        temp_dir: str,
        system_info_update_interval: int,
        account_info_update_interval: int,
    ) -> None:
        self.hf_model_dir = hf_model_dir
        self.external_model_dir = external_model_dir
        self.log_dir = log_dir
        self.temp_dir = temp_dir
        self.system_info_update_interval = system_info_update_interval
        self.account_info_update_interval = account_info_update_interval

    async def _update_system_info(self):
        while True:
            try:
                await update_system_info(
                    hf_model_dir=self.hf_model_dir,
                    external_model_dir=self.external_model_dir,
                    log_dir=self.log_dir,
                    temp_dir=self.temp_dir,
                )
            except TimeoutError:
                pass
            except Exception as e:
                _logger.error("update system info error")
                _logger.exception(e)
            await sleep(self.system_info_update_interval)

    @asynccontextmanager
    async def run(self, app: FastAPI):
        async with create_task_group() as tg:
            tg.start_soon(self._update_system_info)
            tg.start_soon(update_account_info, self.account_info_update_interval)
            yield
            tg.cancel_scope.cancel()
