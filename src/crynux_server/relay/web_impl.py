from inspect import signature
import json
import os
import shutil
import tempfile
from contextlib import ExitStack
from datetime import datetime
from typing import Any, Awaitable, BinaryIO, Callable, Dict, List, Optional, Protocol
from functools import wraps

import httpx
from anyio import open_file, to_thread, wrap_file
from hexbytes import HexBytes
from web3 import Web3

from crynux_server.models import Event, EventType, TaskError, load_event
from crynux_server.models.node import ChainNodeStatus, NodeInfo
from crynux_server.models.task import RelayTask
from crynux_server.utils import get_address_from_privkey

from .abc import Relay
from .exceptions import RelayError
from .sign import Signer


def _process_resp(resp: httpx.Response, method: str):
    try:
        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as e:
        message = str(e)
        if resp.status_code == 400 or resp.status_code == 500:
            try:
                content = resp.json()
                if "data" in content:
                    data = content["data"]
                    message = json.dumps(data)
                elif "message" in content:
                    message = content["message"]
                else:
                    message = resp.text
            except Exception:
                pass
        raise RelayError(resp.status_code, method, message) from e


def _web_relay_restart_pool_error(func):
    @wraps(func)
    async def wrapper(self: 'WebRelay', *args: Any, **kwargs: Any) -> Any:
        try:
            return await func(self, *args, **kwargs)
        except httpx.PoolTimeout as e:
            await self.restart_client()
            raise e

    return wrapper


class WebRelay(Relay):
    def __init__(self, base_url: str, privkey: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=30)
        self.signer = Signer(privkey=privkey)
        self._node_address = get_address_from_privkey(privkey)

    async def restart_client(self):
        await self.client.aclose()
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=30)

    @property
    def node_address(self):
        return self._node_address

    """ task related """

    @_web_relay_restart_pool_error
    async def create_task(
        self,
        task_id_commitment: bytes,
        task_args: str,
        checkpoint_dir: Optional[str] = None,
    ) -> RelayTask:
        task_id_commitment_hex = HexBytes(task_id_commitment).hex()
        input: Dict[str, Any] = {
            "task_id_commitment": task_id_commitment_hex,
            "task_args": task_args,
        }
        timestamp, signature = self.signer.sign(input)
        input.update({"timestamp": timestamp, "signature": signature})

        if checkpoint_dir is not None:
            with tempfile.TemporaryDirectory() as tmp_dir:
                checkpoint_file = os.path.join(tmp_dir, "checkpoint.zip")
                await to_thread.run_sync(
                    shutil.make_archive, checkpoint_file[:-4], "zip", checkpoint_dir
                )
            with ExitStack() as stack:
                filename = os.path.basename(checkpoint_file)
                file_obj = stack.enter_context(open(checkpoint_file, "rb"))
                files = [("checkpoint", (filename, file_obj))]

                resp = await self.client.post(
                    f"/v1/inference_tasks/{task_id_commitment_hex}",
                    data=input,
                    files=files,
                    timeout=None,
                )
        else:
            resp = await self.client.post(
                f"/v1/inference_tasks/{task_id_commitment_hex}", data=input
            )
        resp = _process_resp(resp, "createTask")
        content = resp.json()
        data = content["data"]
        return RelayTask.model_validate(data)

    @_web_relay_restart_pool_error
    async def get_checkpoint(
        self, task_id_commitment: bytes, result_checkpoint_dir: str
    ):
        task_id_commitment_hex = HexBytes(task_id_commitment).hex()
        input = {"task_id_commitment": task_id_commitment_hex}
        timestamp, signature = self.signer.sign(input)

        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_file = os.path.join(tmp_dir, "checkpoint.zip")
            async with await open_file(checkpoint_file, mode="wb") as f:
                resp = await self.client.get(
                    f"/v1/inference_tasks/{task_id_commitment_hex}/checkpoint",
                    params={"timestamp": timestamp, "signature": signature},
                )
                resp = _process_resp(resp, "getCheckpoint")
                async for chunk in resp.aiter_bytes(4096):
                    await f.write(chunk)

            await to_thread.run_sync(
                shutil.unpack_archive, checkpoint_file, result_checkpoint_dir
            )

    @_web_relay_restart_pool_error
    async def get_task(self, task_id_commitment: bytes) -> RelayTask:
        task_id_commitment_hex = HexBytes(task_id_commitment).hex()
        input = {"task_id_commitment": task_id_commitment_hex}
        timestamp, signature = self.signer.sign(input)

        resp = await self.client.get(
            f"/v1/inference_tasks/{task_id_commitment_hex}",
            params={"timestamp": timestamp, "signature": signature},
        )
        resp = _process_resp(resp, "getTask")
        content = resp.json()
        data = content["data"]
        return RelayTask.model_validate(data)

    @_web_relay_restart_pool_error
    async def report_task_error(self, task_id_commitment: bytes, task_error: TaskError):
        task_id_commitment_hex = HexBytes(task_id_commitment).hex()
        input = {"task_id_commitment": task_id_commitment_hex, "task_error": task_error}
        timestamp, signature = self.signer.sign(input)

        resp = await self.client.post(
            f"/v1/inference_tasks/{task_id_commitment_hex}/task_error",
            json={
                "task_error": task_error,
                "timestamp": timestamp,
                "signature": signature,
            },
        )
        resp = _process_resp(resp, "reportTaskError")

    @_web_relay_restart_pool_error
    async def submit_task_score(self, task_id_commitment: bytes, score: bytes):
        task_id_commitment_hex = HexBytes(task_id_commitment).hex()
        score_hex = HexBytes(score).hex()
        input = {"task_id_commitment": task_id_commitment_hex, "score": score_hex}
        timestamp, signature = self.signer.sign(input)

        resp = await self.client.post(
            f"/v1/inference_tasks/{task_id_commitment_hex}/score",
            json={"score": score_hex, "timestamp": timestamp, "signature": signature},
        )
        resp = _process_resp(resp, "submitTaskScore")

    @_web_relay_restart_pool_error
    async def upload_task_result(
        self,
        task_id_commitment: bytes,
        file_paths: List[str],
        checkpoint_dir: Optional[str] = None,
    ):
        task_id_commitment_hex = HexBytes(task_id_commitment).hex()
        input = {"task_id_commitment": task_id_commitment_hex}
        timestamp, signature = self.signer.sign(input)

        with ExitStack() as stack:
            files = []
            for file_path in file_paths:
                filename = os.path.basename(file_path)
                file_obj = stack.enter_context(open(file_path, "rb"))
                files.append(("files", (filename, file_obj)))

            if checkpoint_dir is not None:
                tmp_dir = stack.enter_context(tempfile.TemporaryDirectory())
                checkpoint_file = os.path.join(tmp_dir, "checkpoint.zip")
                await to_thread.run_sync(
                    shutil.make_archive, checkpoint_file[:-4], "zip", checkpoint_dir
                )
                filename = os.path.basename(checkpoint_file)
                file_obj = stack.enter_context(open(checkpoint_file, "rb"))
                files.append(("checkpoint", (filename, file_obj)))

            # disable timeout because there may be many images or image size may be very large
            resp = await self.client.post(
                f"/v1/inference_tasks/{task_id_commitment_hex}/results",
                data={"timestamp": timestamp, "signature": signature},
                files=files,
                timeout=None,
            )
            resp = _process_resp(resp, "uploadTaskResult")
            content = resp.json()
            message = content["message"]
            if message != "success":
                raise RelayError(resp.status_code, "uploadTaskResult", message)

    @_web_relay_restart_pool_error
    async def get_result(self, task_id_commitment: bytes, index: int, dst: BinaryIO):
        task_id_commitment_hex = HexBytes(task_id_commitment).hex()
        input = {"task_id_commitment": task_id_commitment_hex, "index": str(index)}
        timestamp, signature = self.signer.sign(input)

        async_dst = wrap_file(dst)

        async with self.client.stream(
            "GET",
            f"/v1/inference_tasks/{task_id_commitment_hex}/results/{index}",
            params={"timestamp": timestamp, "signature": signature},
        ) as resp:
            resp = _process_resp(resp, "getTask")
            async for chunk in resp.aiter_bytes():
                await async_dst.write(chunk)

    @_web_relay_restart_pool_error
    async def get_result_checkpoint(
        self, task_id_commitment: bytes, result_checkpoint_dir: str
    ):
        task_id_commitment_hex = HexBytes(task_id_commitment).hex()
        input = {"task_id_commitment": task_id_commitment_hex}
        timestamp, signature = self.signer.sign(input)

        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_file = os.path.join(tmp_dir, "checkpoint.zip")
            async with await open_file(checkpoint_file, mode="wb") as f:
                resp = await self.client.get(
                    f"/v1/inference_tasks/{task_id_commitment_hex}/results/checkpoint",
                    params={"timestamp": timestamp, "signature": signature},
                )
                resp = _process_resp(resp, "getResultCheckpoint")
                async for chunk in resp.aiter_bytes(4096):
                    await f.write(chunk)

            await to_thread.run_sync(
                shutil.unpack_archive, checkpoint_file, result_checkpoint_dir
            )

    """ auxiliary """

    @_web_relay_restart_pool_error
    async def now(self) -> int:
        resp = await self.client.get("/v1/now")
        resp = _process_resp(resp, "now")
        content = resp.json()
        data = content["data"]
        now = data["now"]
        return now

    @_web_relay_restart_pool_error
    async def close(self):
        await self.client.aclose()

    """ node related """

    @_web_relay_restart_pool_error
    async def node_get_node_info(self) -> NodeInfo:
        input = {"address": self.node_address}
        timestamp, signature = self.signer.sign(input=input)
        resp = await self.client.get(
            f"/v2/node/{self.node_address}",
            params={"timestamp": timestamp, "signature": signature}
        )
        resp = _process_resp(resp, "nodeGetNodeInfo")
        content = resp.json()
        data = content["data"]
        return NodeInfo.model_validate(data)

    @_web_relay_restart_pool_error
    async def node_join(
        self, network: str, gpu_name: str, gpu_vram: int, model_ids: List[str], version: str, staking_amount: int
    ):
        input = {
            "address": self.node_address,
            "network": network,
            "gpu_name": gpu_name,
            "gpu_vram": gpu_vram,
            "model_ids": model_ids,
            "version": version,
            "staking": str(staking_amount),
        }
        timestamp, signature = self.signer.sign(input)
        resp = await self.client.post(
            f"/v2/node/{self.node_address}/join",
            json={
                "network": network,
                "gpu_name": gpu_name,
                "gpu_vram": gpu_vram,
                "model_ids": model_ids,
                "version": version,
                "staking": str(staking_amount),
                "timestamp": timestamp,
                "signature": signature,
            },
        )
        resp = _process_resp(resp, "nodeJoin")

    @_web_relay_restart_pool_error
    async def node_report_model_downloaded(self, model_id: str):
        input = {"address": self.node_address, "model_id": model_id}
        timestamp, signature = self.signer.sign(input)
        resp = await self.client.post(
            f"/v1/node/{self.node_address}/model",
            json={
                "model_id": model_id,
                "timestamp": timestamp,
                "signature": signature,
            },
        )
        resp = _process_resp(resp, "nodeReportModelDownload")

    @_web_relay_restart_pool_error
    async def node_pause(self):
        input = {"address": self.node_address}
        timestamp, signature = self.signer.sign(input)
        resp = await self.client.post(
            f"/v1/node/{self.node_address}/pause",
            json={"timestamp": timestamp, "signature": signature},
        )
        resp = _process_resp(resp, "nodePause")

    @_web_relay_restart_pool_error
    async def node_quit(self):
        input = {"address": self.node_address}
        timestamp, signature = self.signer.sign(input)
        resp = await self.client.post(
            f"/v1/node/{self.node_address}/quit",
            json={"timestamp": timestamp, "signature": signature},
        )
        resp = _process_resp(resp, "nodeQuit")

    @_web_relay_restart_pool_error
    async def node_resume(self):
        input = {"address": self.node_address}
        timestamp, signature = self.signer.sign(input)
        resp = await self.client.post(
            f"/v1/node/{self.node_address}/resume",
            json={"timestamp": timestamp, "signature": signature},
        )
        resp = _process_resp(resp, "nodeResume")

    @_web_relay_restart_pool_error
    async def node_get_current_task(self) -> bytes:
        resp = await self.client.get(
            f"/v1/node/{self.node_address}/task",
        )
        resp = _process_resp(resp, "getCurrentTask")
        content = resp.json()
        task_id_commitment = content["data"]
        assert isinstance(task_id_commitment, str) and task_id_commitment.startswith(
            "0x"
        )
        return bytes.fromhex(task_id_commitment[2:])

    @_web_relay_restart_pool_error
    async def node_update_version(self, version: str):
        input = {"address": self.node_address, "version": version}
        timestamp, signature = self.signer.sign(input)
        resp = await self.client.post(
            f"/v1/node/{self.node_address}/version",
            json={"version": version, "timestamp": timestamp, "signature": signature},
        )
        resp = _process_resp(resp, "nodeUpdateNodeVersion")

    """ balance related """

    @_web_relay_restart_pool_error
    async def get_balance(self, address: Optional[str] = None) -> int:
        if address is None:
            address = self.node_address
        resp = await self.client.get(
            f"/v1/balance/{address}",
        )
        resp = _process_resp(resp, "getBalance")
        content = resp.json()
        balance = content["data"]
        return Web3.to_wei(balance, "wei")
    
    @_web_relay_restart_pool_error
    async def get_staking_amount(self) -> int:
        resp = await self.client.get(
            f"/v1/staking/{self.node_address}",
        )
        resp = _process_resp(resp, "getStakingAmount")
        content = resp.json()
        staking_amount = content["data"]
        return Web3.to_wei(staking_amount, "wei")

    @_web_relay_restart_pool_error
    async def transfer(self, amount: int, to_addr: str):
        input = {"from": self.node_address, "value": str(amount), "to": to_addr}
        timestamp, signature = self.signer.sign(input)
        resp = await self.client.post(
            f"/v1/balance/{self.node_address}/transfer",
            json={
                "value": str(amount),
                "to": to_addr,
                "timestamp": timestamp,
                "signature": signature,
            },
        )
        resp = _process_resp(resp, "transfer")

    @_web_relay_restart_pool_error
    async def get_events(
        self,
        start_id: int,
        event_type: Optional[EventType] = None,
        node_address: Optional[str] = None,
        task_id_commitment: Optional[bytes] = None,
        limit: Optional[int] = None,
    ) -> List[Event]:
        input: Dict[str, Any] = {"start": start_id}
        if event_type is not None:
            input["event_type"] = event_type
        if node_address is not None:
            input["node_address"] = node_address
        if task_id_commitment is not None:
            input["task_id_commitment"] = "0x" + task_id_commitment.hex()
        if limit is not None:
            input["limit"] = limit

        resp = await self.client.get("/v1/events", params=input)
        resp = _process_resp(resp, "getEvents")
        content = resp.json()
        data = content["data"]

        events = []
        for e in data:
            id = e["id"]
            task_type = e["type"]
            args = e["args"]
            events.append(load_event(id, task_type, args))
        return events

    @_web_relay_restart_pool_error
    async def get_current_event_id(
        self,
        event_type: Optional[EventType] = None,
        node_address: Optional[str] = None,
        task_id_commitment: Optional[bytes] = None,
    ) -> int:
        input = {}
        if event_type is not None:
            input["event_type"] = event_type
        if node_address is not None:
            input["node_address"] = node_address
        if task_id_commitment is not None:
            input["task_id_commitment"] = "0x" + task_id_commitment.hex()

        resp = await self.client.get("/v1/events/current_id", params=input)
        resp = _process_resp(resp, "getCurrentEventID")
        content = resp.json()
        data = content["data"]

        return data