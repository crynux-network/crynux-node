from typing import TYPE_CHECKING, List, Optional

from eth_typing import ChecksumAddress
from web3 import AsyncWeb3, Web3

from crynux_server.models import ChainNodeStakingInfo

from .utils import ContractWrapper, TxWaiter
from .w3_pool import W3Pool

if TYPE_CHECKING:
    from crynux_server.config import TxOption


__all__ = ["NodeStakingContract"]



class NodeStakingContract(ContractWrapper):
    def __init__(
        self, w3_pool: W3Pool, contract_address: Optional[ChecksumAddress] = None
    ):
        super().__init__(w3_pool, "NodeStaking", contract_address)

    async def set_admin_address(
        self,
        addr: ChecksumAddress,
        *,
        option: "Optional[TxOption]" = None,
        w3: Optional[AsyncWeb3] = None,
    ):
        return await self._transaction_call(
            "setAdminAddress",
            addr=addr,
            option=option,
            w3=w3,
        )

    async def get_min_stake_amount(self, *, w3: Optional[AsyncWeb3] = None) -> int:
        return await self._function_call(
            "getMinStakeAmount",
            w3=w3,
        )

    async def get_staking_info(
        self, node_address: ChecksumAddress, *, w3: Optional[AsyncWeb3] = None
    ) -> ChainNodeStakingInfo:
        res = await self._function_call(
            "getStakingInfo",
            nodeAddress=node_address,
            w3=w3,
        )
        return ChainNodeStakingInfo(
            node_address=res[0],
            staked_balance=res[1],
            staked_credits=res[2],
        )

    async def get_all_node_addresses(
        self, *, w3: Optional[AsyncWeb3] = None
    ) -> List[ChecksumAddress]:
        return await self._function_call(
            "getAllNodeAddresses",
            w3=w3,
        )

    async def stake(
        self,
        staked_amount: int,
        *,
        value: Optional[int] = None,
        option: "Optional[TxOption]" = None,
        w3: Optional[AsyncWeb3] = None,
    ):
        return await self._transaction_call(
            "stake",
            stakedAmount=staked_amount,
            value=value,
            option=option,
            w3=w3,
        )

    async def unstake(
        self,
        node_address: str,
        *,
        option: "Optional[TxOption]" = None,
        w3: Optional[AsyncWeb3] = None,
    ):
        return await self._transaction_call(
            "unstake",
            nodeAddress=node_address,
            option=option,
            w3=w3,
        )

    async def slash_staking(
        self,
        node_address: ChecksumAddress,
        *,
        option: "Optional[TxOption]" = None,
        w3: Optional[AsyncWeb3] = None,
    ):
        return await self._transaction_call(
            "slashStaking",
            nodeAddress=node_address,
            option=option,
            w3=w3,
        )
