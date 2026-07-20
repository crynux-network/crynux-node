if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()

import logging
import math
import os.path
import signal
from typing import Optional

import anyio
from anyio import (TASK_STATUS_IGNORED, Event, create_task_group,
                   get_cancelled_exc_class, move_on_after, sleep)
from anyio.abc import TaskGroup, TaskStatus

from crynux_server import db, log, utils
from crynux_server.config import apply_server_env, get_config, with_proxy
from crynux_server.node_manager import NodeManager, set_node_manager
from crynux_server.server import Server, set_server
from crynux_server.worker_manager import WorkerManager, set_worker_manager

_logger = logging.getLogger(__name__)


class CrynuxRunner(object):
    def __init__(self) -> None:
        apply_server_env()
        self.config = get_config()

        log.init(
            self.config.log.dir,
            self.config.log.level,
            self.config.log.filename,
        )
        _logger.debug("Logger init completed.")

        self._server: Optional[Server] = None
        self._node_manager: Optional[NodeManager] = None
        self._tg: Optional[TaskGroup] = None

        self._shutdown_event: Optional[Event] = None
        self._should_shutdown = False
        signal.signal(signal.SIGINT, self._shutdown_signal_handler)
        signal.signal(signal.SIGTERM, self._shutdown_signal_handler)

    def _shutdown_signal_handler(self, *args):
        self._should_shutdown = True

    async def _check_should_shutdown(self):
        while not self._should_shutdown:
            await sleep(0.1)
        self._set_shutdown_event()

    def _set_shutdown_event(self):
        if self._shutdown_event is not None:
            self._shutdown_event.set()

    async def _wait_for_shutdown(self):
        if self._shutdown_event is not None:
            await self._shutdown_event.wait()
            await self._stop()

    async def run(self, task_status: TaskStatus[None] = TASK_STATUS_IGNORED):
        assert self._tg is None, "Crynux Server is running"

        _logger.info("Starting Crynux server")

        with with_proxy(self.config):
            self._shutdown_event = Event()

            await db.init(self.config.db)
            _logger.info("DB init completed.")

            inference_worker_manager = WorkerManager(
                role="inference", config=self.config
            )
            set_worker_manager(inference_worker_manager)
            download_worker_manager = WorkerManager(
                role="download", config=self.config
            )
            set_worker_manager(download_worker_manager)

            _logger.info(f"Serving WebUI from: {os.path.abspath(self.config.web_dist)}")
            self._server = Server(
                hf_model_dir=self.config.task_config.hf_cache_dir,
                external_model_dir=self.config.task_config.external_cache_dir,
                log_dir=self.config.log.dir,
                temp_dir=self.config.task_config.output_dir,
                web_dist=self.config.web_dist,
            )
            set_server(self._server)
            _logger.info("Web server init completed.")

            gpu_info = await utils.get_gpu_info()
            gpu_name = utils.apply_gpu_name_executor_marker(gpu_info)
            gpu_vram_gb = math.ceil(gpu_info.vram_total_mb / 1024)
            platform = utils.get_platform()

            _logger.info("Starting node manager...")

            self._node_manager = NodeManager(
                config=self.config, platform=platform, gpu_name=gpu_name, gpu_vram=gpu_vram_gb
            )
            set_node_manager(self._node_manager)

            _logger.info("Node manager created.")

            try:
                async with create_task_group() as tg:
                    self._tg = tg

                    tg.start_soon(self._check_should_shutdown)
                    tg.start_soon(self._wait_for_shutdown)

                    await tg.start(
                        self._server.start,
                        self.config.server_host,
                        self.config.server_port,
                        self.config.log.level == "DEBUG",
                    )
                    _logger.info("Crynux server started.")
                    task_status.started()
                    tg.start_soon(self._node_manager.run)
            except get_cancelled_exc_class() as e:
                _logger.debug("Crynux server stopped for being cancelled")
                _logger.debug(e, exc_info=True)
            except Exception as e:
                _logger.debug("Crynux server stopped by exception")
                _logger.debug(e, exc_info=True)
            finally:
                with move_on_after(2, shield=True):
                    await db.close()
                with move_on_after(10, shield=True):
                    await self._node_manager.close()
                self._shutdown_event = None
                self._tg = None
                _logger.info("Crynux server stopped")

    async def _stop(self):
        _logger.info("Stopping crynux server")
        if self._tg is None:
            return

        if self._server is not None:
            self._server.stop()
            _logger.info("stop server")
        if self._node_manager is not None:
            with move_on_after(10, shield=True):
                await self._node_manager.stop()
                _logger.info("stop node manager")
        self._tg.cancel_scope.cancel()
        _logger.info("cancel runner task group")

    async def stop(self):
        self._set_shutdown_event()


def run():
    try:
        runner = CrynuxRunner()
        anyio.run(runner.run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
