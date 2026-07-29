from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from crynux_server import models
from crynux_server.node_manager.state_manager import NodeStateManager


def make_state_manager():
    config = MagicMock()
    state_cache = MagicMock()
    state_cache.set_node_state = AsyncMock()
    state_cache.set_tx_state = AsyncMock()
    state_cache.get_tx_state = AsyncMock(
        return_value=SimpleNamespace(status=models.TxStatus.Success)
    )
    download_model_cache = MagicMock()
    download_model_cache.load_all = AsyncMock(
        return_value=[
            SimpleNamespace(
                model=SimpleNamespace(to_model_id=lambda: "base:model-a")
            )
        ]
    )
    contracts = MagicMock()
    relay = MagicMock()
    relay.node_resume = AsyncMock()
    relay.node_sync_capabilities = AsyncMock()
    manager = NodeStateManager(
        config=config,
        state_cache=state_cache,
        download_model_cache=download_model_cache,
        contracts=contracts,
        relay=relay,
    )
    return manager, relay


async def test_try_start_syncs_capabilities_when_already_joined():
    manager, relay = make_state_manager()
    manager._get_node_status = AsyncMock(return_value=models.NodeStatus.Running)

    await manager.try_start(
        gpu_name="RTX 4090+docker",
        gpu_vram=24,
        version=[3, 2, 0],
    )

    relay.node_sync_capabilities.assert_awaited_once_with(
        gpu_name="RTX 4090+docker",
        gpu_vram=24,
        model_ids=["base:model-a"],
        version="3.2.0",
    )
    relay.node_resume.assert_not_awaited()


async def test_try_start_syncs_capabilities_after_resume():
    manager, relay = make_state_manager()
    manager._get_node_status = AsyncMock(
        side_effect=[
            models.NodeStatus.Paused,
            models.NodeStatus.Paused,
            models.NodeStatus.Paused,
            models.NodeStatus.Running,
        ]
    )

    await manager.try_start(
        gpu_name="RTX 4090+docker",
        gpu_vram=24,
        version=[3, 2, 0],
    )

    relay.node_resume.assert_awaited_once()
    relay.node_sync_capabilities.assert_awaited_once_with(
        gpu_name="RTX 4090+docker",
        gpu_vram=24,
        model_ids=["base:model-a"],
        version="3.2.0",
    )
