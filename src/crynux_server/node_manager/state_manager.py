import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from anyio import CancelScope, Lock, fail_after, get_cancelled_exc_class, sleep
from tenacity import retry, stop_never, wait_fixed
from web3 import Web3

from crynux_server import models
from crynux_server.config import Config, get_staking_amount
from crynux_server.contracts import Contracts, TxOption
from crynux_server.contracts.exceptions import TxRevertedError
from crynux_server.download_model_cache import DownloadModelCache
from crynux_server.relay.abc import Relay
from crynux_server.relay.exceptions import RelayError

from .state_cache import ManagerStateCache

_logger = logging.getLogger(__name__)


class TxSessionError(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class NodeStateManager(object):
    def __init__(
        self,
        config: Config,
        state_cache: ManagerStateCache,
        download_model_cache: DownloadModelCache,
        contracts: Contracts,
        relay: Relay,
    ):
        self.config = config
        self.state_cache = state_cache
        self.download_model_cache = download_model_cache
        self.contracts = contracts
        self.relay = relay

        self._tx_lock = Lock()

        self._sync_node_status_scope = None
        self._auto_stake_scope = None

    async def _get_node_status(self):
        node_info = await self.relay.node_get_node_info()
        remote_status = node_info.status
        local_status = models.convert_node_status(remote_status)
        return local_status

    async def _wait_for_running(self):
        local_status = await self._get_node_status()
        assert (
            local_status == models.NodeStatus.Running
        ), "Node status on chain is not running."
        await self.state_cache.set_node_state(local_status, slashed=False)
        await self.state_cache.set_tx_state(models.TxStatus.Success)

    async def _wait_for_stop(self):
        pending = True

        while True:
            local_status = await self._get_node_status()
            assert local_status in [
                models.NodeStatus.Stopped,
                models.NodeStatus.PendingStop,
            ], "Node status on chain is not stopped or pending."
            await self.state_cache.set_node_state(local_status)
            if pending:
                await self.state_cache.set_tx_state(models.TxStatus.Success)
                pending = False
            if local_status == models.NodeStatus.Stopped:
                break

    async def _wait_for_pause(self):
        pending = True

        while True:
            local_status = await self._get_node_status()
            assert local_status in [
                models.NodeStatus.Paused,
                models.NodeStatus.PendingPause,
            ], "Node status on chain is not paused or pending"
            await self.state_cache.set_node_state(local_status)
            if pending:
                await self.state_cache.set_tx_state(models.TxStatus.Success)
                pending = False
            if local_status == models.NodeStatus.Paused:
                break

    async def start_sync_node_status(self, interval: float = 5):
        assert self._sync_node_status_scope is None

        try:
            with CancelScope() as scope:
                self._sync_node_status_scope = scope
                while True:
                    node_info = await self.relay.node_get_node_info()
                    remote_status = node_info.status
                    local_status = models.convert_node_status(remote_status)
                    current_status = (await self.state_cache.get_node_state()).status
                    if (
                        current_status == models.NodeStatus.KickedOut
                        or current_status == models.NodeStatus.Slashed
                    ):
                        current_status = models.NodeStatus.Stopped
                    if local_status != current_status:
                        await self.state_cache.set_node_state(local_status)
                    node_score_state = models.NodeScoreState(
                        qos_score=node_info.qos_score,
                        staking_score=node_info.staking_score,
                        prob_weight=node_info.prob_weight,
                    )
                    await self.state_cache.set_node_score_state(node_score_state)
                    await sleep(interval)
        finally:
            self._sync_node_status_scope = None

    def stop_sync_node_status(self):
        if self._sync_node_status_scope is not None:
            self._sync_node_status_scope.cancel()

    async def start_auto_stake(self, interval: float = 5):
        assert self._auto_stake_scope is None

        try:
            with CancelScope() as scope:
                self._auto_stake_scope = scope
                staking_info = (
                    await self.contracts.node_staking_contract.get_staking_info(
                        self.contracts.account
                    )
                )
                current_staking_amount = (
                    staking_info.staked_balance + staking_info.staked_credits
                )
                while True:
                    staking_amount = Web3.to_wei(get_staking_amount(), "ether")
                    node_status = (await self.state_cache.get_node_state()).status
                    if staking_amount != current_staking_amount:
                        if node_status not in [
                            models.NodeStatus.Stopped,
                            models.NodeStatus.PendingStop,
                            models.NodeStatus.Slashed,
                            models.NodeStatus.KickedOut,
                        ]:
                            async with self._tx_session():
                                waiter = await self.contracts.stake(staking_amount)
                                assert waiter is not None
                                await waiter.wait()
                                await self.state_cache.set_tx_state(
                                    models.TxStatus.Success
                                )
                        current_staking_amount = staking_amount
                        _logger.info(f"Staking amount updated to {staking_amount}")
                    await sleep(interval)
        finally:
            self._auto_stake_scope = None

    def stop_auto_stake(self):
        if self._auto_stake_scope is not None:
            self._auto_stake_scope.cancel()

    @asynccontextmanager
    async def _tx_session(
        self,
        *,
        expected_node_status: Optional[models.NodeStatus] = None,
        set_pending: bool = True,
    ):
        if expected_node_status is not None:
            node_status = await self._get_node_status()
            assert node_status == expected_node_status, TxSessionError(
                f"Node status is not {expected_node_status}"
            )

        tx_status = (await self.state_cache.get_tx_state()).status
        assert tx_status != models.TxStatus.Pending, TxSessionError(
            "Last transaction is in pending"
        )
        async with self._tx_lock:
            if expected_node_status is not None:
                node_status = await self._get_node_status()
                assert node_status == expected_node_status, TxSessionError(
                    f"Node status is not {expected_node_status}"
                )

            tx_status = (await self.state_cache.get_tx_state()).status
            assert tx_status != models.TxStatus.Pending, TxSessionError(
                "Last transaction is in pending"
            )
            if set_pending:
                await self.state_cache.set_tx_state(models.TxStatus.Pending)

            async with self._wrap_tx_error():
                yield

    @asynccontextmanager
    async def _wrap_tx_error(self):
        try:
            yield
        except KeyboardInterrupt:
            raise
        except get_cancelled_exc_class():
            raise
        except (RelayError, AssertionError, ValueError, TxRevertedError) as e:
            _logger.exception(
                "tx error: %s: %s", type(e).__name__, str(e) or repr(e)
            )
            with fail_after(5, shield=True):
                await self.state_cache.set_tx_state(models.TxStatus.Error, str(e))
            raise
        except Exception as e:
            _logger.exception(
                "unknown tx error: %s: %s", type(e).__name__, str(e) or repr(e)
            )
            raise

    async def _ensure_start_balance(self, staking_amount: int):
        balance = await self.contracts.get_balance(self.contracts.account)
        credits = await self.contracts.credits_contract.get_credits(
            self.contracts.account
        )
        total_balance = balance + credits

        staking_info = await self.contracts.node_staking_contract.get_staking_info(
            self.contracts.account
        )
        current_staking_amount = (
            staking_info.staked_balance + staking_info.staked_credits
        )

        if total_balance + current_staking_amount < staking_amount + Web3.to_wei(
            0.001, "ether"
        ):
            raise ValueError("Node token balance is not enough to join")

    async def _send_stake_tx(
        self, staking_amount: int, *, option: "Optional[TxOption]" = None
    ):
        stake_value = await self.contracts.get_stake_tx_value(staking_amount)
        await self.state_cache.set_tx_state(models.TxStatus.Pending)
        if stake_value is None:
            return None
        return await self.contracts.node_staking_contract.stake(
            staking_amount, value=stake_value, option=option
        )

    async def try_start(
        self,
        gpu_name: str,
        gpu_vram: int,
        version: List[int],
        *,
        option: "Optional[TxOption]" = None,
    ):
        _logger.info("Trying to join the network automatically...")
        last_logged_start_status: Optional[models.NodeStatus] = None

        @retry(wait=wait_fixed(1), stop=stop_never, reraise=True)
        async def _start():
            nonlocal last_logged_start_status
            status = await self._get_node_status()
            if status in [
                models.NodeStatus.Running,
                models.NodeStatus.PendingPause,
                models.NodeStatus.PendingStop,
            ]:
                _logger.info("Node has joined in the network.")
                await self.state_cache.set_node_state(status)
                return

            elif status == models.NodeStatus.Stopped:
                if last_logged_start_status != status:
                    _logger.info("Node is stopped, trying to join the network...")
                    last_logged_start_status = status
                async with self._tx_session(
                    expected_node_status=models.NodeStatus.Stopped,
                    set_pending=False,
                ):
                    staking_amount = Web3.to_wei(get_staking_amount(), "ether")
                    await self._ensure_start_balance(staking_amount)
                    waiter = await self._send_stake_tx(
                        staking_amount, option=option
                    )
                    if waiter is not None:
                        await waiter.wait()

                    download_models = await self.download_model_cache.load_all()
                    model_ids = [model.model.to_model_id() for model in download_models]
                    await self.relay.node_join(
                        network=self.config.ethereum.network,
                        gpu_name=gpu_name,
                        gpu_vram=gpu_vram,
                        version=".".join(str(v) for v in version),
                        model_ids=model_ids,
                        staking_amount=staking_amount,
                    )
                    # update tx state to avoid the web user controlling node status by api
                    # it's the same in try_stop method
                    await self._wait_for_running()
            elif status == models.NodeStatus.Paused:
                if last_logged_start_status != status:
                    _logger.info("Node is paused, trying to resume...")
                    last_logged_start_status = status
                async with self._tx_session(
                    expected_node_status=models.NodeStatus.Paused
                ):
                    await self.relay.node_resume()
                    await self._wait_for_running()

            _logger.info("Node joins in the network successfully.")

        await _start()

    async def try_stop(self, *, option: "Optional[TxOption]" = None):
        @retry(wait=wait_fixed(1), stop=stop_never, reraise=True)
        async def _stop():
            node_info = await self.relay.node_get_node_info()
            remote_status = node_info.status

            if remote_status == models.ChainNodeStatus.AVAILABLE:
                async with self._tx_session(
                    expected_node_status=models.NodeStatus.Running
                ):  
                    staking_info = await self.contracts.node_staking_contract.get_staking_info(
                        self.contracts.account
                    )
                    if staking_info.status == models.ChainNodeStakingStatus.Staked:
                        waiter = await self.contracts.node_staking_contract.try_unstake(option=option)
                        await waiter.wait()
                    await self.relay.node_quit()
                    await self._wait_for_stop()
                _logger.info("Node leaves the network successfully.")
            elif remote_status == models.ChainNodeStatus.QUIT:
                _logger.info("Node has already left the network.")
            else:
                _logger.info(
                    f"Node status is {models.convert_node_status(remote_status)}, cannot leave the network automatically"
                )

        await _stop()

    async def start(
        self,
        gpu_name: str,
        gpu_vram: int,
        version: List[int],
        *,
        option: "Optional[TxOption]" = None,
    ):
        async with self._tx_session(
            expected_node_status=models.NodeStatus.Stopped,
            set_pending=False,
        ):
            staking_amount = Web3.to_wei(get_staking_amount(), "ether")
            await self._ensure_start_balance(staking_amount)
            waiter = await self._send_stake_tx(staking_amount, option=option)

            async def wait():
                async with self._wrap_tx_error():
                    if waiter is not None:
                        await waiter.wait()
                    download_models = await self.download_model_cache.load_all()
                    model_ids = [model.model.to_model_id() for model in download_models]
                    await self.relay.node_join(
                        network=self.config.ethereum.network,
                        gpu_name=gpu_name,
                        gpu_vram=gpu_vram,
                        model_ids=model_ids,
                        version=".".join(str(v) for v in version),
                        staking_amount=staking_amount,
                    )
                    await self._wait_for_running()

            return wait

    async def stop(
        self,
        *,
        option: "Optional[TxOption]" = None,
    ):
        async with self._tx_session(expected_node_status=models.NodeStatus.Running):
            staking_info = await self.contracts.node_staking_contract.get_staking_info(
                self.contracts.account
            )
            waiter = None
            if staking_info.status == models.ChainNodeStakingStatus.Staked:
                waiter = await self.contracts.node_staking_contract.try_unstake(option=option)

            async def wait():
                async with self._wrap_tx_error():
                    if waiter is not None:
                        await waiter.wait()
                    await self.relay.node_quit()
                    await self._wait_for_stop()

            return wait

    async def pause(
        self,
        *,
        option: "Optional[TxOption]" = None,
    ):
        async with self._tx_session(expected_node_status=models.NodeStatus.Running):
            await self.relay.node_pause()

            async def wait():
                async with self._wrap_tx_error():
                    await self._wait_for_pause()

            return wait

    async def resume(
        self,
        *,
        option: "Optional[TxOption]" = None,
    ):
        async with self._tx_session(expected_node_status=models.NodeStatus.Paused):
            await self.relay.node_resume()

            async def wait():
                async with self._wrap_tx_error():
                    await self._wait_for_running()

            return wait


_default_state_manager: Optional[NodeStateManager] = None


def get_node_state_manager() -> NodeStateManager:
    assert _default_state_manager is not None, "NodeStateManager has not been set."

    return _default_state_manager


def set_node_state_manager(manager: NodeStateManager):
    global _default_state_manager

    _default_state_manager = manager
