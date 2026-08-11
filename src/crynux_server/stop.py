import logging
from typing import Type, Optional

import anyio

from crynux_server import db, log, models
from crynux_server.config import get_config, TxOption
from crynux_server.contracts import Contracts
from crynux_server.node_manager import ManagerStateCache, NodeStateManager
from crynux_server.node_manager.state_cache import (
    DbNodeStateCache,
    DbTxStateCache,
    StateCache,
)
from crynux_server.download_model_cache import DownloadModelCache, DbDownloadModelCache
from crynux_server.relay import Relay, WebRelay

_logger = logging.getLogger(__name__)


async def _stop(
    state_manager: Optional[NodeStateManager] = None,
    contracts: Optional[Contracts] = None,
    relay: Optional[Relay] = None,
    state_cache: Optional[ManagerStateCache] = None,
    base_url: Optional[str] = None,
    node_state_cache_cls: Type[StateCache[models.NodeState]] = DbNodeStateCache,
    tx_state_cache_cls: Type[StateCache[models.TxState]] = DbTxStateCache,
    download_model_cache_cls: Type[DownloadModelCache] = DbDownloadModelCache,
    option: Optional[TxOption] = None,
):
    config = get_config()

    log.init(config.log.dir, config.log.level, config.log.filename)

    await db.init(config.db)

    if state_manager is None:
        if contracts is None:
            assert (
                len(config.ethereum.privkey) > 0
            ), "You must provide private key in config file before stoping node."
            contracts = Contracts(
                provider_path=config.ethereum.provider,
                privkey=config.ethereum.privkey,
            )
            await contracts.init(
                benefit_address_contract_address=config.ethereum.contract.benefit_address,
                delegated_staking_contract_address=config.ethereum.contract.delegated_staking,
                node_staking_contract_address=config.ethereum.contract.node_staking,
            )
        if relay is None:
            assert base_url is not None
            relay = WebRelay(base_url, config.ethereum.privkey)
        if state_cache is None:
            state_cache = ManagerStateCache(
                node_state_cache_cls=node_state_cache_cls,
                tx_state_cache_cls=tx_state_cache_cls,
            )
        download_model_cache = download_model_cache_cls()
        state_manager = NodeStateManager(config=config, state_cache=state_cache, download_model_cache=download_model_cache, contracts=contracts, relay=relay)

    try:
        waiter = await state_manager.stop(option=option)
        await waiter()
        _logger.info("Stop the node successfully")
    except Exception:
        _logger.error("Cannot stop the node")
        raise
    finally:
        await state_manager.contracts.close()


def stop(
    state_manager: Optional[NodeStateManager] = None,
    contracts: Optional[Contracts] = None,
    state_cache: Optional[ManagerStateCache] = None,
    node_state_cache_cls: Type[StateCache[models.NodeState]] = DbNodeStateCache,
    tx_state_cache_cls: Type[StateCache[models.TxState]] = DbTxStateCache,
    option: Optional[TxOption] = None,
):
    try:
        anyio.run(
            _stop,
            state_manager,
            contracts,
            state_cache,
            node_state_cache_cls,
            tx_state_cache_cls,
            option,
        )
    except KeyboardInterrupt:
        pass
