import logging
from enum import IntEnum
from typing import Any, Dict, Optional

from anyio import Condition
from eth_keys.datatypes import PrivateKey, PublicKey
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3.logs import WARN
from web3.providers.async_base import AsyncBaseProvider
from web3.types import TxParams, TxReceipt, BlockIdentifier, BlockData

from crynux_server.config import TxOption

from . import benefit_address, credits, delegated_staking, node_staking
from .exceptions import TxRevertedError
from .utils import ContractWrapper, TxWaiter
from .w3_pool import W3Pool

__all__ = [
    "TxRevertedError",
    "Contracts",
    "TxWaiter",
    "get_contracts",
    "set_contracts",
    "ContractWrapper",
    "wait_contracts",
]

_logger = logging.getLogger(__name__)


class ProviderType(IntEnum):
    HTTP = 0
    WS = 1
    Other = 2


class Contracts(object):
    benefit_address_contract: benefit_address.BenefitAddressContract
    credits_contract: credits.CreditsContract
    delegated_staking_contract: delegated_staking.DelegatedStakingContract
    node_staking_contract: node_staking.NodeStakingContract

    def __init__(
        self,
        privkey: str,
        provider: Optional[AsyncBaseProvider] = None,
        provider_path: Optional[str] = None,
        pool_size: int = 5,
        timeout: int = 10,
        rps: int = 10,
    ):
        if provider is not None:
            pool_size = 1

        self._w3_pool = W3Pool(
            privkey=privkey,
            provider=provider,
            provider_path=provider_path,
            pool_size=pool_size,
            timeout=timeout,
            rps=rps,
        )

        self._initialized = False
        self._closed = False

    async def init(
        self,
        credits_contract_address: Optional[str] = None,
        benefit_address_contract_address: Optional[str] = None,
        delegated_staking_contract_address: Optional[str] = None,
        node_staking_contract_address: Optional[str] = None,
        *,
        option: "Optional[TxOption]" = None,
    ):
        try:
            async with await self._w3_pool.get() as w3:
                assert w3.eth.default_account, "Wallet address is empty"
                self._account = w3.eth.default_account
                _logger.info(f"Wallet address is {w3.eth.default_account}")

                if (
                    credits_contract_address is None
                    or benefit_address_contract_address is None
                    or delegated_staking_contract_address is None
                    or node_staking_contract_address is None
                ):
                    raise ValueError("all contract addresses must be provided")

                self.benefit_address_contract = (
                    benefit_address.BenefitAddressContract(
                        self._w3_pool,
                        w3.to_checksum_address(benefit_address_contract_address),
                    )
                )

                self.credits_contract = credits.CreditsContract(
                    self._w3_pool, w3.to_checksum_address(credits_contract_address)
                )

                self.delegated_staking_contract = (
                    delegated_staking.DelegatedStakingContract(
                        self._w3_pool,
                        w3.to_checksum_address(delegated_staking_contract_address),
                    )
                )

                self.node_staking_contract = node_staking.NodeStakingContract(
                    self._w3_pool,
                    w3.to_checksum_address(node_staking_contract_address),
                )

                self._initialized = True

                return self
        except Exception:
            await self.close()
            raise

    async def close(self):
        if not self._closed:
            await self._w3_pool.close()
            self._closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self.close()

    def get_contract(self, name: str):
        if name == "credits":
            return self.credits_contract
        elif name == "node_staking":
            return self.node_staking_contract
        else:
            raise ValueError(f"unknown contract name {name}")

    async def get_events(
        self,
        contract_name: str,
        event_name: str,
        filter_args: Optional[Dict[str, Any]] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
    ):
        contract = self.get_contract(contract_name)
        return await contract.get_events(
            event_name=event_name,
            filter_args=filter_args,
            from_block=from_block,
            to_block=to_block,
        )

    async def event_process_receipt(
        self, contract_name: str, event_name: str, recepit: TxReceipt, errors=WARN
    ):
        contract = self.get_contract(contract_name)
        return await contract.event_process_receipt(
            event_name=event_name, recepit=recepit, errors=errors
        )

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def account(self) -> ChecksumAddress:
        return self._w3_pool.account

    @property
    def public_key(self) -> PublicKey:
        return self._w3_pool.public_key

    @property
    def private_key(self) -> PrivateKey:
        return self._w3_pool._privkey

    async def get_current_block_number(self) -> int:
        async with await self._w3_pool.get() as w3:
            return await w3.eth.get_block_number()

    async def get_block(self, block_identifier: BlockIdentifier) -> BlockData:
        async with await self._w3_pool.get() as w3:
            block = await w3.eth.get_block(block_identifier=block_identifier)
            return block

    async def get_tx_receipt(self, tx_hash: HexBytes) -> TxReceipt:
        async with await self._w3_pool.get() as w3:
            receipt = await w3.eth.get_transaction_receipt(tx_hash)
            return receipt

    async def get_balance(self, account: ChecksumAddress) -> int:
        async with await self._w3_pool.get() as w3:
            return await w3.eth.get_balance(account)

    async def transfer(
        self, to: str, amount: int, *, option: "Optional[TxOption]" = None
    ):
        async with await self._w3_pool.get() as w3:
            opt: TxParams = {}
            if option is not None:
                opt.update(**option)
            opt["to"] = w3.to_checksum_address(to)
            opt["from"] = self._w3_pool.account
            opt["value"] = w3.to_wei(amount, "Wei")
            async with self._w3_pool.with_nonce(w3) as nonce:
                opt["nonce"] = nonce
            tx_hash = await w3.eth.send_transaction(opt)
            receipt = await w3.eth.wait_for_transaction_receipt(tx_hash)
            return receipt
        
    async def get_stake_tx_value(self, amount: int) -> Optional[int]:
        async with await self._w3_pool.get() as w3:
            value = 0
            current_staking_info = await self.node_staking_contract.get_staking_info(
                self._w3_pool.account, w3=w3
            )
            current_staking_amount = current_staking_info.staked_balance + current_staking_info.staked_credits
            if amount == current_staking_amount:
                return None

            min_staking_amount = await self.node_staking_contract.get_min_stake_amount(w3=w3)
            if amount < min_staking_amount:
                raise ValueError(
                    f"Staking amount is below the chain minimum staking amount ({min_staking_amount} wei)"
                )
            
            if amount > current_staking_amount:
                diff = amount - current_staking_amount
                stakable_credits = await self.credits_contract.get_credits(self._w3_pool.account, w3=w3)
                if stakable_credits < diff:
                    value = diff - stakable_credits

            return value

    async def stake(self, amount: int, *, option: "Optional[TxOption]" = None):
        value = await self.get_stake_tx_value(amount)
        if value is None:
            return None
        return await self.node_staking_contract.stake(amount, value=value, option=option)

_default_contracts: Optional[Contracts] = None


_condition: Optional[Condition] = None


def _get_condition() -> Condition:
    global _condition

    if _condition is None:
        _condition = Condition()

    return _condition


def get_contracts() -> Contracts:
    assert _default_contracts is not None, "Contracts has not been set."

    return _default_contracts


async def set_contracts(contracts: Contracts):
    global _default_contracts

    condition = _get_condition()
    async with condition:
        _default_contracts = contracts
        condition.notify_all()


async def wait_contracts():
    condition = _get_condition()
    async with condition:
        while _default_contracts is None:
            await condition.wait()

        return _default_contracts
