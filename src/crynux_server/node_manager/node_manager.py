from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, Type

from anyio import (
    TASK_STATUS_IGNORED,
    Event,
    create_task_group,
    fail_after,
    get_cancelled_exc_class,
    move_on_after,
    sleep,
    to_thread,
)
from anyio.abc import TaskGroup, TaskStatus
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    stop_after_attempt,
    stop_never,
    wait_fixed,
)
from web3 import Web3

from crynux_server import models
from crynux_server.config import (
    Config,
    ensure_staking_amount,
    get_staking_amount,
    wait_privkey,
)
from crynux_server.contracts import Contracts, set_contracts
from crynux_server.relay import Relay, WebRelay, set_relay
from crynux_server.task import (
    DbDownloadTaskStateCache,
    DbInferenceTaskStateCache,
    DownloadTaskStateCache,
    InferenceTaskStateCache,
    TaskSystem,
    set_download_task_state_cache,
    set_inference_task_state_cache,
    set_task_system,
)
from crynux_server.watcher import EventWatcher, set_watcher
from crynux_server.worker_manager import (
    TaskCancelled,
    TaskDownloadError,
    TaskError,
    WorkerManager,
    get_worker_manager,
)
from crynux_server.download_model_cache import (
    DownloadModelCache,
    DbDownloadModelCache,
    set_download_model_cache,
)

from .state_cache import (
    DbNodeStateCache,
    DbTxStateCache,
    ManagerStateCache,
    StateCache,
    set_manager_state_cache,
    DbNodeScoreStateCache,
)
from .state_manager import NodeStateManager, set_node_state_manager

_logger = logging.getLogger(__name__)


async def _make_contracts(
    privkey: str,
    provider: str,
    timeout: int,
    rps: int,
    benefit_address_contract_address: Optional[str],
    credits_contract_address: Optional[str],
    delegated_staking_contract_address: Optional[str],
    node_staking_contract_address: Optional[str],
) -> Contracts:
    contracts = Contracts(
        provider_path=provider, privkey=privkey, timeout=timeout, rps=rps
    )
    await contracts.init(
        benefit_address_contract_address=benefit_address_contract_address,
        credits_contract_address=credits_contract_address,
        delegated_staking_contract_address=delegated_staking_contract_address,
        node_staking_contract_address=node_staking_contract_address,
    )
    await set_contracts(contracts)
    return contracts


def _make_relay(privkey: str, relay_url: str) -> Relay:
    relay = WebRelay(base_url=relay_url, privkey=privkey)
    set_relay(relay)
    return relay


async def _make_watcher(
    relay: Relay,
    fetch_interval: int = 1,
) -> EventWatcher:
    watcher = EventWatcher(relay=relay, fetch_interval=fetch_interval)

    set_watcher(watcher)
    return watcher


def _make_task_system(
    retry: bool,
    contracts: Contracts,
    relay: Relay,
    inference_state_cache_cls: Type[InferenceTaskStateCache],
    download_state_cache_cls: Type[DownloadTaskStateCache],
) -> TaskSystem:
    inference_state_cache = inference_state_cache_cls()
    set_inference_task_state_cache(inference_state_cache)
    download_state_cache = download_state_cache_cls()
    set_download_task_state_cache(download_state_cache)

    system = TaskSystem(
        inference_state_cache=inference_state_cache,
        download_state_cache=download_state_cache,
        contracts=contracts,
        relay=relay,
        retry=retry,
    )

    set_task_system(system)
    return system


def _make_node_state_manager(
    config: Config,
    state_cache: ManagerStateCache,
    download_model_cache: DownloadModelCache,
    contracts: Contracts,
    relay: Relay,
):
    state_manager = NodeStateManager(
        config=config,
        state_cache=state_cache,
        download_model_cache=download_model_cache,
        contracts=contracts,
        relay=relay,
    )
    set_node_state_manager(state_manager)
    return state_manager


# Manage node, including:
# 1. node start up/shut down, joining into the network
# 2. node status changes/management
# 3. task generation, distribution and execution
# 4. event fetching and processing, etc.
class NodeManager(object):
    def __init__(
        self,
        config: Config,
        platform: str,
        gpu_name: str,
        gpu_vram: int,
        inference_state_cache_cls: Type[
            InferenceTaskStateCache
        ] = DbInferenceTaskStateCache,
        download_state_cache_cls: Type[
            DownloadTaskStateCache
        ] = DbDownloadTaskStateCache,
        node_state_cache_cls: Type[StateCache[models.NodeState]] = DbNodeStateCache,
        tx_state_cache_cls: Type[StateCache[models.TxState]] = DbTxStateCache,
        node_score_state_cache_cls: Type[
            StateCache[models.NodeScoreState]
        ] = DbNodeScoreStateCache,
        download_model_cache_cls: Type[DownloadModelCache] = DbDownloadModelCache,
        manager_state_cache: Optional[ManagerStateCache] = None,
        privkey: Optional[str] = None,
        contracts: Optional[Contracts] = None,
        relay: Optional[Relay] = None,
        node_state_manager: Optional[NodeStateManager] = None,
        watcher: Optional[EventWatcher] = None,
        task_system: Optional[TaskSystem] = None,
        worker_manager: Optional[WorkerManager] = None,
        retry: bool = True,
        retry_delay: float = 30,
    ) -> None:
        self.config = config
        self.platform = platform
        self.gpu_name = gpu_name
        self.gpu_vram = gpu_vram

        self.inference_state_cache_cls = inference_state_cache_cls
        self.download_state_cache_cls = download_state_cache_cls

        self.download_model_cache = download_model_cache_cls()
        set_download_model_cache(self.download_model_cache)
        if manager_state_cache is None:
            manager_state_cache = ManagerStateCache(
                node_state_cache_cls=node_state_cache_cls,
                tx_state_cache_cls=tx_state_cache_cls,
                node_score_state_cache_cls=node_score_state_cache_cls,
            )
            set_manager_state_cache(manager_state_cache)
        self.state_cache = manager_state_cache

        self._privkey = privkey
        self._contracts = contracts
        self._relay = relay
        self._node_state_manager = node_state_manager
        self._watcher = watcher
        self._task_system = task_system
        if worker_manager is None:
            worker_manager = get_worker_manager()
        self._worker_manager = worker_manager

        self._retry = retry
        self._retry_delay = retry_delay

        self._tg: Optional[TaskGroup] = None
        self._finish_event: Optional[Event] = None

        self._stoped = False

    @property
    def finish_event(self) -> Event:
        if self._finish_event is None:
            self._finish_event = Event()
        return self._finish_event

    async def _init_components(self):
        _logger.info("Initializing node manager components.")

        if self._contracts is None or self._relay is None:
            if self._privkey is None:
                self._privkey = await wait_privkey()

            if self._contracts is None:
                self._contracts = await _make_contracts(
                    privkey=self._privkey,
                    provider=self.config.ethereum.provider,
                    timeout=self.config.ethereum.timeout,
                    rps=self.config.ethereum.rps,
                    benefit_address_contract_address=self.config.ethereum.contract.benefit_address,
                    credits_contract_address=self.config.ethereum.contract.credits,
                    delegated_staking_contract_address=self.config.ethereum.contract.delegated_staking,
                    node_staking_contract_address=self.config.ethereum.contract.node_staking,
                )
                min_staking_amount = (
                    await self._contracts.node_staking_contract.get_min_stake_amount()
                )
                staking_amount = await to_thread.run_sync(
                    ensure_staking_amount, min_staking_amount
                )
                _logger.info("Staking amount is %s CNX.", staking_amount)
            if self._relay is None:
                self._relay = _make_relay(self._privkey, self.config.relay_url)

        if self._task_system is None:
            self._task_system = _make_task_system(
                retry=self._retry,
                contracts=self._contracts,
                relay=self._relay,
                inference_state_cache_cls=self.inference_state_cache_cls,
                download_state_cache_cls=self.download_state_cache_cls,
            )

        if self._node_state_manager is None:
            self._node_state_manager = _make_node_state_manager(
                config=self.config,
                state_cache=self.state_cache,
                download_model_cache=self.download_model_cache,
                contracts=self._contracts,
                relay=self._relay,
            )

        if self._watcher is None:
            self._watcher = await _make_watcher(relay=self._relay)

        _logger.info("Node manager components initializing complete.")

    async def _prefetch_models(self):
        preload_models = self.config.task_config.preloaded_models
        task_inputs = []
        if preload_models is not None:
            if preload_models.sd_base is not None:
                for model in preload_models.sd_base:
                    task_input = models.TaskInput(
                        task=models.DownloadTaskInput(
                            task_name="download",
                            task_type=models.TaskType.SD,
                            task_id=f"preload_models_{len(task_inputs)}",
                            model=models.ModelConfig(
                                id=model.id, type="base", variant=model.variant
                            ),
                        )
                    )
                    task_inputs.append(task_input)
            if preload_models.gpt_base is not None:
                for model in preload_models.gpt_base:
                    task_input = models.TaskInput(
                        task=models.DownloadTaskInput(
                            task_name="download",
                            task_type=models.TaskType.LLM,
                            task_id=f"preload_models_{len(task_inputs)}",
                            model=models.ModelConfig(
                                id=model.id, type="base", variant=model.variant
                            ),
                        )
                    )
                    task_inputs.append(task_input)
            if preload_models.controlnet is not None:
                for model in preload_models.controlnet:
                    task_input = models.TaskInput(
                        task=models.DownloadTaskInput(
                            task_name="download",
                            task_type=models.TaskType.SD,
                            task_id=f"preload_models_{len(task_inputs)}",
                            model=models.ModelConfig(
                                id=model.id, type="controlnet", variant=model.variant
                            ),
                        )
                    )
                    task_inputs.append(task_input)
            if preload_models.lora is not None:
                for model in preload_models.lora:
                    task_input = models.TaskInput(
                        task=models.DownloadTaskInput(
                            task_name="download",
                            task_type=models.TaskType.SD,
                            task_id=f"preload_models_{len(task_inputs)}",
                            model=models.ModelConfig(
                                id=model.id, type="lora", variant=model.variant
                            ),
                        )
                    )
                    task_inputs.append(task_input)

        for i, task_input in enumerate(task_inputs):
            task_fut = await self._worker_manager.send_task(task_input)
            try:
                msg = f"Downloading models............ ({i+1}/{len(task_inputs)})"
                _logger.info(msg)
                await self.state_cache.set_node_state(
                    status=models.NodeStatus.Init, init_message=msg
                )
                await task_fut.get()
                await self.download_model_cache.save(
                    models.DownloadedModel(
                        task_type=task_input.task.task_type, model=task_input.task.model
                    )
                )
            except TaskCancelled:
                raise ValueError(
                    "Failed to download models due to worker internal error"
                )
            except TaskDownloadError as e:
                raise ValueError(
                    "Failed to download models due to network issue"
                ) from e
            except Exception as e:
                raise ValueError("Failed to download models") from e

    async def _run_initial_inference_task(self):
        prompt = (
            "a realistic photo of an old man sitting on a brown chair, "
            "on the seaside, with blue sky and white clouds, a dog is lying "
            "under his legs, masterpiece, high resolution"
        )
        task_args = {
            "version": "3.0.0",
            "base_model": {
                "name": "crynux-network/stable-diffusion-v1-5",
                "variant": "fp16",
            },
            "prompt": prompt,
            "negative_prompt": "",
            "task_config": {
                "num_images": 1,
                "safety_checker": False,
                "cfg": 7,
                "seed": 99975892,
                "steps": 40,
            },
        }

        task_input = models.TaskInput(
            task=models.InferenceTaskInput(
                task_name="inference",
                task_type=models.TaskType.SD,
                task_id="initial_inference_task",
                models=[
                    models.ModelConfig(
                        id="crynux-network/stable-diffusion-v1-5",
                        type="base",
                        variant="fp16",
                    )
                ],
                task_args=json.dumps(task_args),
                output_dir=self.config.task_config.output_dir,
            )
        )
        try:
            with fail_after(300):
                task_fut = await self._worker_manager.send_task(task_input)
                await task_fut.get()
        except TimeoutError as e:
            msg = (
                "The initial inference task exceeded the timeout limit(5 min). Maybe your device does not meet "
                "the lowest hardware requirements"
            )
            raise ValueError(msg) from e
        except TaskError as e:
            raise ValueError("The initial validation task failed") from e

    async def _init(self):
        _logger.info("Initialize node manager")

        async for attemp in AsyncRetrying(
            stop=stop_after_attempt(3) if self._retry else stop_after_attempt(1),
            wait=wait_fixed(1),
            before_sleep=before_sleep_log(_logger, logging.ERROR, exc_info=True),
            reraise=True,
        ):
            with attemp:
                await self._prefetch_models()
        _logger.info("Finish downloading models")

        await self.state_cache.set_node_state(
            status=models.NodeStatus.Init, init_message="Running local evaluation task"
        )
        await self._run_initial_inference_task()
        _logger.info("Finish initial validation task")

        _logger.info("Node manager initializing complete.")

    async def _sync_node_status(self):
        assert self._node_state_manager is not None

        async for attemp in AsyncRetrying(
            stop=stop_never if self._retry else stop_after_attempt(1),
            wait=wait_fixed(self._retry_delay),
            reraise=True,
        ):
            with attemp:
                try:
                    if attemp.retry_state.attempt_number > 0:
                        await self.state_cache.set_node_state(
                            status=models.NodeStatus.Running
                        )
                    await self._node_state_manager.start_sync_node_status()
                except Exception as e:
                    _logger.exception(e)
                    _logger.error("Cannot sync node state from chain, retrying")
                    with fail_after(5, shield=True):
                        await self.state_cache.set_node_state(
                            status=models.NodeStatus.Error,
                            message="Node manager running error: cannot sync node state from chain, retrying...",
                        )
                    raise

    async def _auto_stake(self):
        assert self._node_state_manager is not None

        async for attemp in AsyncRetrying(
            stop=stop_never if self._retry else stop_after_attempt(1),
            wait=wait_fixed(self._retry_delay),
            reraise=True,
        ):
            with attemp:
                try:
                    if attemp.retry_state.attempt_number > 0:
                        await self.state_cache.set_node_state(
                            status=models.NodeStatus.Running
                        )
                    await self._node_state_manager.start_auto_stake()
                except Exception as e:
                    _logger.exception(e)
                    _logger.error("Cannot auto update staking amount, retrying")
                    with fail_after(5, shield=True):
                        await self.state_cache.set_node_state(
                            status=models.NodeStatus.Error,
                            message="Node manager running error: cannot auto update staking amount, retrying...",
                        )
                    raise

    async def _watch_events(
        self, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED
    ):
        assert self._watcher is not None
        assert self._relay is not None

        account = self._relay.node_address

        async def _node_kicked_out(event: models.Event):
            assert isinstance(event, models.NodeKickedOut)
            address = event.node_address
            if address == account:
                _logger.info("Node is kicked out")
                await self.state_cache.set_node_state(
                    status=models.NodeStatus.KickedOut, message="Node is kicked out"
                )

        self._watcher.add_event_filter("NodeKickedOut", callback=_node_kicked_out)

        async def _node_slashed(event: models.Event):
            assert isinstance(event, models.NodeSlashed)
            address = event.node_address
            if address == account:
                _logger.info("Node is slashed")
                await self.state_cache.set_node_state(
                    status=models.NodeStatus.Slashed,
                    message="Node is slashed",
                    slashed=True,
                )

        self._watcher.add_event_filter("NodeSlashed", callback=_node_slashed)

        async def _download_task_started(event: models.Event):
            assert isinstance(event, models.DownloadModel)
            assert self._task_system is not None
            address = event.node_address
            model_id = event.model_id
            task_type = event.task_type
            task_id = f"{address}_{model_id}"
            await self._task_system.create_download_task(task_id, task_type, model_id)

        self._watcher.add_event_filter("DownloadModel", _download_task_started)

        # call task_status.started() only once
        task_status_set = False

        async for attemp in AsyncRetrying(
            stop=stop_never if self._retry else stop_after_attempt(1),
            wait=wait_fixed(self._retry_delay),
            reraise=True,
        ):
            with attemp:
                try:
                    if attemp.retry_state.attempt_number > 0:
                        await self.state_cache.set_node_state(
                            status=models.NodeStatus.Running
                        )
                    async with create_task_group() as tg:
                        if not task_status_set:
                            await tg.start(self._watcher.start)
                            task_status.started()
                            task_status_set = True
                        else:
                            await self._watcher.start()
                except Exception as e:
                    _logger.exception(e)
                    _logger.error("Cannot watch events from chain, retrying")
                    with fail_after(5, shield=True):
                        await self.state_cache.set_node_state(
                            status=models.NodeStatus.Error,
                            message="Node manager running error: cannot watch events from chain, retrying...",
                        )
                    raise

    async def _check_time(self):
        assert self._relay is not None
        remote_now = 0
        async for attemp in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_fixed(self._retry_delay),
            reraise=True,
        ):
            with attemp:
                try:
                    remote_now = await self._relay.now()
                except Exception as e:
                    _logger.exception(e)
                    _logger.error(f"Cannot get server time from relay")
                    raise ValueError("Cannot get server time from relay")
        now = int(datetime.now().timestamp())
        diff = now - remote_now
        if abs(diff) > 60:
            raise ValueError(
                f"The difference between local time and server time is too large ({diff})"
            )

    async def _can_join_network(self) -> bool:
        node_amount = Web3.to_wei(get_staking_amount(), "ether")

        assert self._relay is not None
        assert self._contracts is not None
        async for attemp in AsyncRetrying(
            stop=stop_never if self._retry else stop_after_attempt(1),
            wait=wait_fixed(self._retry_delay),
            reraise=True,
        ):
            with attemp:
                try:
                    node_info = await self._relay.node_get_node_info()
                    staking_info = (
                        await self._contracts.node_staking_contract.get_staking_info(
                            self._contracts.account
                        )
                    )
                    if (
                        node_info.status != models.ChainNodeStatus.QUIT
                        and staking_info.status
                        in [
                            models.ChainNodeStakingStatus.PendingUnstaked,
                            models.ChainNodeStakingStatus.Unstaked,
                        ]
                    ):  
                        _logger.info("Node is unstaked but not quit, quitting now...")
                        await self._relay.node_quit()
                        return False

                    if node_info.status != models.ChainNodeStatus.QUIT:
                        return True
                    balance = await self._contracts.get_balance(self._contracts.account)
                    credits = await self._contracts.credits_contract.get_credits(
                        self._contracts.account
                    )
                    if staking_info.status == models.ChainNodeStakingStatus.Staked:
                        current_staking_amount = (
                            staking_info.staked_balance + staking_info.staked_credits
                        )
                    else:
                        current_staking_amount = 0
                    total_balance = balance + credits
                    if total_balance + current_staking_amount >= node_amount:
                        return True
                except Exception as e:
                    _logger.exception(e)
                    _logger.error(
                        "Cannot connect to the blockchain when checking node status and balance, retrying..."
                    )
                    if attemp.retry_state.attempt_number > 3:
                        with fail_after(5, shield=True):
                            await self.state_cache.set_node_state(
                                status=models.NodeStatus.Error,
                                message="Cannot connect to the blockchain, retrying...consider using a proxy server.",
                            )
                    raise
        return False

    async def _update_version(self):

        async def _update():
            assert self._relay is not None
            current_version = self._worker_manager.version
            while True:
                async with self._worker_manager.wait_connection_changed():
                    version = self._worker_manager.version
                    if version is not None and version != current_version:
                        # check version name, should be like "x.y.z"
                        version_list = [int(v) for v in version.split(".")]
                        assert len(version_list) == 3
                        # update version
                        await self._relay.node_update_version(version)
                    current_version = version

        async for attemp in AsyncRetrying(
            stop=stop_never if self._retry else stop_after_attempt(1),
            wait=wait_fixed(self._retry_delay),
            reraise=True,
        ):
            with attemp:
                try:
                    await _update()
                except Exception as e:
                    _logger.exception(e)
                    _logger.error(f"Cannot get update node version, retrying")
                    with fail_after(5, shield=True):
                        await self.state_cache.set_node_state(
                            status=models.NodeStatus.Error,
                            message="Node manager running error: cannot get update node version, retrying...",
                        )
                    raise

    async def _run(self, prefetch: bool = True):
        assert self._tg is None, "Node manager is running."

        _logger.debug("Starting node manager...")

        try:
            async with create_task_group() as tg:
                self._tg = tg

                try:
                    async with self._worker_manager.wait_connected(timeout=30):
                        version = self._worker_manager.version
                        assert version is not None
                        version_list = [int(v) for v in version.split(".")]
                        assert len(version_list) == 3
                except TimeoutError:
                    raise ValueError("Worker is not connected within 30 seconds")

                try:
                    async with create_task_group() as init_tg:
                        await self.state_cache.set_node_state(models.NodeStatus.Init)
                        # clear tx error when restart
                        # set tx status to pending to forbid user to control node from web
                        await self.state_cache.set_tx_state(models.TxStatus.Success)

                        if prefetch:
                            init_tg.start_soon(self._init)

                        await self._init_components()
                        await self._check_time()

                except get_cancelled_exc_class():
                    _logger.exception(f"Node manager init error: init task cancelled")
                    raise
                except Exception as e:
                    _logger.exception(e)
                    msg = f"Node manager init error: {repr(e)}"
                    _logger.error(msg)
                    with fail_after(5, shield=True):
                        await self.state_cache.set_node_state(
                            models.NodeStatus.Error, msg
                        )
                    await self.stop()
                    return

                await self.state_cache.set_node_state(
                    models.NodeStatus.Init,
                    init_message="Synchronizing node status from the blockchain",
                )

                assert self._task_system is not None
                tg.start_soon(self._task_system.start)

                # wait the balance is enough to join the network or node has joined the network
                while not await self._can_join_network():
                    await self.state_cache.set_node_state(
                        status=models.NodeStatus.Stopped
                    )
                    await sleep(5)

                await tg.start(self._watch_events)

                assert self._node_state_manager is not None

                await self.state_cache.set_node_state(
                    status=models.NodeStatus.Init,
                    init_message="Joining the network",
                )
                async for attemp in AsyncRetrying(
                    stop=stop_never if self._retry else stop_after_attempt(1),
                    wait=wait_fixed(self._retry_delay),
                    reraise=True,
                ):
                    with attemp:
                        try:
                            await self._node_state_manager.try_start(
                                gpu_name=self.gpu_name + "+" + self.platform,
                                gpu_vram=self.gpu_vram,
                                version=version_list,
                            )
                        except Exception as e:
                            _logger.warning(e)
                            _logger.info("Cannot auto join the network")
                            raise e

                tg.start_soon(self._sync_node_status)
                tg.start_soon(self._auto_stake)
                tg.start_soon(self._update_version)

        finally:
            self._tg = None

    async def run(self, prefetch: bool = True):
        assert self._tg is None, "Node manager is running."

        try:
            with self._worker_manager.start():
                await self._run(prefetch=prefetch)
        except get_cancelled_exc_class():
            raise
        except Exception as e:
            _logger.exception(e)
            msg = f"Node manager running error: {repr(e)}"
            _logger.error(msg)
            with fail_after(5, shield=True):
                await self.state_cache.set_node_state(models.NodeStatus.Error, msg)
            await self.stop()
        finally:
            _logger.info("node manager is stopped")

    async def stop(self):
        if not self._stoped:
            try:
                if self._task_system is not None:
                    self._task_system.stop()
                    self._task_system = None
                if self._node_state_manager is not None:
                    with move_on_after(10, shield=True):
                        await self._node_state_manager.try_stop()
                    self._node_state_manager.stop_sync_node_status()
                    self._node_state_manager.stop_auto_stake()
                    self._node_state_manager = None

                if self._tg is not None and not self._tg.cancel_scope.cancel_called:
                    self._tg.cancel_scope.cancel()

            finally:
                self._stoped = True

    async def close(self):
        if self._relay is not None:
            with fail_after(2, shield=True):
                await self._relay.close()
            self._relay = None
        if self._contracts is not None:
            await self._contracts.close()
            self._contracts = None


_node_manager: Optional[NodeManager] = None


def get_node_manager() -> NodeManager:
    assert _node_manager is not None, "Node manager has not been set."

    return _node_manager


def set_node_manager(manager: NodeManager):
    global _node_manager

    _node_manager = manager
