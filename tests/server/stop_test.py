import pytest

from crynux_server.models import NodeStatus
from crynux_server.config import Config, set_config
from crynux_server.contracts import Contracts
from crynux_server.node_manager import NodeStateManager, ManagerStateCache
from crynux_server.node_manager.state_cache import MemoryNodeStateCache, MemoryTxStateCache
from crynux_server.stop import _stop
from crynux_server.faucet import MockFaucet

@pytest.fixture
def tx_option():
    return {}


@pytest.fixture
def test_config():
    test_config = Config.model_validate(
        {
            "log": {
                "dir": "logs",
                "level": "INFO",
                "max_bytes": 52428800,
                "backup_count": 5,
            },
            "ethereum": {
                "privkey": "",
                "provider": "",
                "chain_id": None,
                "gas": None,
                "gas_price": None,
                "contract": {"token": "", "node": "", "task": ""},
            },
            "task_dir": "task",
            "db": "",
            "relay_url": "",
            "faucet_url": "",
            "celery": {"broker": "", "backend": ""},
            "distributed": False,
            "task_config": {
                "output_dir": "build/data/images",
                "hf_cache_dir": "build/data/huggingface",
                "external_cache_dir": "build/data/external",
                "inference_logs_dir": "build/data/inference-logs",
                "script_dir": "stable-diffusion-task",
                "result_url": "",
            },
        }
    )
    set_config(test_config)
    return test_config


@pytest.fixture
def privkey():
    return "0xa627246a109551432ac5db6535566af34fdddfaa11df17b8afd53eb987e209a2"


@pytest.fixture
async def node_contracts(privkey, tx_option):
    from web3 import Web3
    from web3.providers.eth_tester import AsyncEthereumTesterProvider
    from eth_account import Account

    provider = AsyncEthereumTesterProvider()
    root_contracts = Contracts(provider=provider, default_account_index=0)

    await root_contracts.init(option=tx_option)

    waiter = await root_contracts.node_contract.update_task_contract_address(
        root_contracts.task_contract.address, option=tx_option
    )
    await waiter.wait()

    provider.ethereum_tester.add_account(privkey)
    account = Account.from_key(privkey)
    amount = Web3.to_wei(1000, "ether")
    await root_contracts.transfer(account.address, amount, option=tx_option)

    node_contract_address = root_contracts.node_contract.address
    task_contract_address = root_contracts.task_contract.address
    qos_contract_address = root_contracts.task_contract.address
    task_queue_contract_address = root_contracts.task_queue_contract.address
    netstats_contract_address = root_contracts.netstats_contract.address

    contracts = Contracts(provider=provider, privkey=privkey)
    await contracts.init(
        node_contract_address=node_contract_address,
        task_contract_address=task_contract_address,
        qos_contract_address=qos_contract_address,
        task_queue_contract_address=task_queue_contract_address,
        netstats_contract_address=netstats_contract_address,
        option=tx_option,
    )
    try:
        yield contracts
    finally:
        await contracts.close()



@pytest.fixture
def state_cache():
    return ManagerStateCache(
        node_state_cache_cls=MemoryNodeStateCache, tx_state_cache_cls=MemoryTxStateCache
    )


@pytest.fixture
async def state_manager(node_contracts, state_cache):
    return NodeStateManager(state_cache=state_cache, contracts=node_contracts)


async def test_stop(tx_option, test_config, state_manager):
    await state_manager.state_cache.set_node_state(NodeStatus.Stopped)
    waiter = await state_manager.start("NVIDIA GeForce GTX 1070 Ti", 8, option=tx_option)
    await waiter()

    await _stop(state_manager=state_manager)

    state = await state_manager.state_cache.get_node_state()
    assert state.status == NodeStatus.Stopped
