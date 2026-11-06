from typing import List

import pytest
from anyio import create_task_group, sleep
from eth_account import Account
from fastapi.testclient import TestClient
from web3 import Web3

from crynux_server import models
from crynux_server.config import Config, TxOption, set_config, set_privkey
from crynux_server.contracts import Contracts, set_contracts
from crynux_server.event_queue import MemoryEventQueue, set_event_queue
from crynux_server.node_manager import (
    NodeManager,
    set_node_manager,
    NodeStateManager,
    set_node_state_manager,
)
from crynux_server.node_manager.state_cache import (
    MemoryNodeStateCache,
    MemoryTxStateCache,
    ManagerStateCache,
    set_manager_state_cache,
)
from crynux_server.relay import MockRelay, Relay, set_relay
from crynux_server.server import Server
from crynux_server.task import (
    MemoryTaskStateCache,
    MockTaskRunner,
    TaskSystem,
    set_task_state_cache,
    set_task_system,
)
from crynux_server.watcher import EventWatcher, MemoryBlockNumberCache, set_watcher
from crynux_server.faucet import MockFaucet, set_faucet

@pytest.fixture
def tx_option():
    return {}


@pytest.fixture
def accounts():
    return [
        "0x577887519278199ce8F8D80bAcc70fc32b48daD4",
        "0x9229d36c82E4e1d03B086C27d704741D0c78321e",
        "0xEa1A669fd6A705d28239011A074adB3Cfd6cd82B",
    ]


@pytest.fixture
def privkeys():
    return [
        "0xa627246a109551432ac5db6535566af34fdddfaa11df17b8afd53eb987e209a2",
        "0xb171f296622b98cbdc08dcdcb0696f738c3a22d9d367c657117cd3c8d0b71d42",
        "0x8fb2fc9862b93b5b75cda8202f583711201e4cba5459eefe442b8c5dcc4bdab9",
    ]


@pytest.fixture
def provider():
    from web3.providers.eth_tester import AsyncEthereumTesterProvider

    provider = AsyncEthereumTesterProvider()
    return provider


@pytest.fixture
async def root_contracts(provider, tx_option, privkeys):
    c0 = Contracts(provider=provider, default_account_index=0)

    await c0.init(option=tx_option)

    waiter = await c0.node_contract.update_task_contract_address(
        c0.task_contract.address, option=tx_option
    )
    await waiter.wait()

    for privkey in privkeys:
        provider.ethereum_tester.add_account(privkey)
        account = Account.from_key(privkey)
        amount = Web3.to_wei(1000, "ether")
        await c0.transfer(account.address, amount, option=tx_option)

    try:
        yield c0
    finally:
        await c0.close()


@pytest.fixture
def config():
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
            "headless": True,
            "task_config": {
                "output_dir": "build/data/images",
                "hf_cache_dir": "build/data/huggingface",
                "external_cache_dir": "build/data/external",
                "inference_logs_dir": "build/data/inference-logs",
                "script_dir": "remote-lora-scripts",
                "result_url": "",
            },
        }
    )
    set_config(test_config)
    return test_config


@pytest.fixture
async def node_contracts(
    provider, root_contracts: Contracts, tx_option: TxOption, privkeys: List[str]
):
    node_contract_address = root_contracts.node_contract.address
    task_contract_address = root_contracts.task_contract.address
    qos_contract_address = root_contracts.task_contract.address
    task_queue_contract_address = root_contracts.task_queue_contract.address
    netstats_contract_address = root_contracts.netstats_contract.address

    cs = []
    for privkey in privkeys:
        contracts = Contracts(provider=provider, privkey=privkey)
        await contracts.init(
            node_contract_address=node_contract_address,
            task_contract_address=task_contract_address,
            qos_contract_address=qos_contract_address,
            task_queue_contract_address=task_queue_contract_address,
            netstats_contract_address=netstats_contract_address,
            option=tx_option,
        )
        cs.append(contracts)
    try:
        yield cs
    finally:
        for c in cs:
            await c.close()


@pytest.fixture
def relay():
    relay = MockRelay()
    set_relay(relay)
    return relay


@pytest.fixture
def gpu_name():
    return "NVIDIA GeForce GTX 1070 Ti"


@pytest.fixture
def gpu_vram():
    return 8


@pytest.fixture
async def managers(
    privkeys: List[str], node_contracts: List[Contracts], relay: Relay, config: Config, gpu_name: str, gpu_vram: int
):
    managers: List[NodeManager] = []

    for i, (privkey, contracts) in enumerate(zip(privkeys, node_contracts)):
        if i == 0:
            await set_privkey(privkeys[0])
            await set_contracts(contracts)
        queue = MemoryEventQueue()
        if i == 0:
            set_event_queue(queue)

        watcher = EventWatcher.from_contracts(contracts)
        block_number_cache = MemoryBlockNumberCache()
        watcher.set_blocknumber_cache(block_number_cache)
        if i == 0:
            set_watcher(watcher)

        def make_callback(queue):
            async def _push_event(event_data):
                event = models.load_event_from_contracts(event_data)
                await queue.put(event)

            return _push_event

        watcher.watch_event(
            "task",
            "TaskStarted",
            callback=make_callback(queue),
            filter_args={"selectedNode": contracts.account},
        )

        task_state_cache = MemoryTaskStateCache()
        system = TaskSystem(
            task_state_cache,
            queue=queue,
            task_name="mock_inference",
        )
        if i == 0:
            set_task_state_cache(task_state_cache)
            set_task_system(system)

        system.set_runner_cls(MockTaskRunner)

        state_cache = ManagerStateCache(
            node_state_cache_cls=MemoryNodeStateCache,
            tx_state_cache_cls=MemoryTxStateCache,
        )
        if i == 0:
            set_manager_state_cache(state_cache)
        # set init state to stopped to bypass prefetch stage
        await state_cache.set_node_state(models.NodeStatus.Stopped)

        faucet = MockFaucet()
        set_faucet(faucet)

        state_manager = NodeStateManager(
            state_cache=state_cache, contracts=contracts
        )
        if i == 0:
            set_node_state_manager(state_manager)

        manager = NodeManager(
            config=config,
            gpu_name=gpu_name,
            gpu_vram=gpu_vram,
            manager_state_cache=state_cache,
            privkey=privkey,
            event_queue=queue,
            contracts=contracts,
            relay=relay,
            faucet=faucet,
            watcher=watcher,
            task_system=system,
            node_state_manager=state_manager,
            retry=False
        )
        if i == 0:
            set_node_manager(manager)
        managers.append(manager)

    return managers


@pytest.fixture
async def running_client(managers):
    client = TestClient(Server().app)
    async with create_task_group() as tg:
        for manager in managers:
            tg.start_soon(manager.run, False)
        await sleep(0.1)
        yield client
        for manager in managers:
            await manager.finish()
        tg.cancel_scope.cancel()


@pytest.fixture
def client(config: Config):
    state_cache = ManagerStateCache(
        node_state_cache_cls=MemoryNodeStateCache, tx_state_cache_cls=MemoryTxStateCache
    )
    set_manager_state_cache(state_cache)
    client = TestClient(Server().app)
    yield client
    client.close()
