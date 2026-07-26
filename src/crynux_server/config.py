from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from functools import partial
from typing import Any, Dict, List, Literal, Tuple, Type, TypedDict, Optional

import yaml
from anyio import Condition, to_thread
from dotenv import dotenv_values
from pydantic import BaseModel, computed_field, Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from web3 import Web3
from web3.types import Wei

__all__ = [
    "Config",
    "get_config",
    "set_config",
    "wait_privkey",
    "set_privkey",
    "get_privkey",
    "TxOption",
    "get_default_tx_option",
    "get_staking_amount",
    "ensure_staking_amount",
    "set_staking_amount",
    "set_task_error_report_automatic",
    "env_file_path",
    "load_env_file",
    "apply_server_env",
]


_data_dir: str = ""
_config_dir: str = "config"
ETHER_WEI = 10**18
_logger = logging.getLogger(__name__)


def config_file_path():
    return os.path.join(_data_dir, _config_dir, "config.yml")


def env_file_path():
    return os.path.join(_data_dir, _config_dir, ".env")


def load_env_file(prefix: str) -> Dict[str, str]:
    env_file = env_file_path()
    if not os.path.exists(env_file):
        return {}

    values = dotenv_values(env_file)
    return {
        key.removeprefix(prefix): value
        for key, value in values.items()
        if key.startswith(prefix) and value is not None
    }


def apply_server_env():
    envs = load_env_file("SERVER_")
    os.environ.update(envs)
    if len(envs) > 0:
        _logger.info(
            "Applied server environment variables from config .env: %s",
            ", ".join(sorted(envs.keys())),
        )


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """
    A simple settings source class that loads variables from a YAML file

    Note: slightly adapted version of JsonConfigSettingsSource from docs.
    """

    _yaml_data: Dict[str, Any] | None = None

    # def __init__(self, settings_cls: type[BaseSettings]):
    #     super().__init__(settings_cls)

    @property
    def yaml_data(self) -> Dict[str, Any]:
        if self._yaml_data is None:
            yaml_file = config_file_path()
            if yaml_file is not None and os.path.exists(yaml_file):
                with open(yaml_file, mode="r", encoding="utf-8") as f:
                    self._yaml_data = yaml.safe_load(f)
            else:
                self._yaml_data = {}
        return self._yaml_data  # type: ignore

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> Tuple[Any, str, bool]:
        field_value = self.yaml_data.get(field_name)
        return field_value, field_name, False

    def prepare_field_value(
        self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
    ) -> Any:
        return value

    def __call__(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}

        for field_name, field in self.settings_cls.model_fields.items():
            field_value, field_key, value_is_complex = self.get_field_value(
                field, field_name
            )
            field_value = self.prepare_field_value(
                field_name, field, field_value, value_is_complex
            )
            if field_value is not None:
                d[field_key] = field_value

        return d


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "FATAL", "CRITICAL"]
DBDriver = Literal["sqlite"]


def set_data_dir(dirname: str):
    global _data_dir

    _data_dir = dirname


class LogConfig(BaseModel):
    m_dir: str = Field(alias="dir")
    level: LogLevel
    filename: str = "crynux-server.log"

    @computed_field
    @property
    def dir(self) -> str:
        return os.path.abspath(os.path.join(_data_dir, self.m_dir))


class DBConfig(BaseModel):
    driver: DBDriver
    m_filename: str = Field(alias="filename")

    @computed_field
    @property
    def filename(self) -> str:
        return os.path.abspath(os.path.join(_data_dir, self.m_filename))

    @computed_field
    @property
    def connection(self) -> str:
        if self.driver == "sqlite":
            return f"sqlite+aiosqlite:///{self.filename}"
        else:
            raise ValueError(f"unsupported db driver {self.driver}")


class Contract(BaseModel):
    benefit_address: str
    credits: str
    node_staking: str
    delegated_staking: str


class Ethereum(BaseModel):
    provider: str
    rps: int = 10
    timeout: int = 30

    chain_id: Optional[int] = None
    gas: Optional[int] = None
    gas_price: Optional[int] = None
    max_fee_per_gas: Optional[int] = None
    max_priority_fee_per_gas: Optional[int] = None

    network: str

    contract: Contract

    _privkey_file: str = "private_key.txt"
    _privkey: str = ""

    @property
    def privkey(self) -> str:
        if len(self._privkey) == 0:
            privkey_file = os.path.join(_data_dir, _config_dir, self._privkey_file)
            if os.path.exists(privkey_file):
                with open(privkey_file, mode="r", encoding="utf-8") as f:
                    self._privkey = f.read().strip()
        return self._privkey

    @privkey.setter
    def privkey(self, privkey: str):
        self._privkey = privkey

    def dump_privkey(self, privkey: str):
        privkey_file = os.path.join(_data_dir, _config_dir, self._privkey_file)
        with open(privkey_file, mode="w", encoding="utf-8") as f:
            f.write(privkey)


class TaskConfig(BaseModel):
    _hf_cache_dir: str = "tmp/huggingface"
    _external_cache_dir: str = "tmp/external"
    _script_dir: str = "worker"
    _output_dir: str = "tmp/results"
    _worker_pid_file: str = "tmp/crynux_worker.pid"

    worker_patch_url: str

    preloaded_models: Optional[PreloadedModelsConfig] = None

    proxy: Optional[ProxyConfig] = None

    tp_fallback: Literal["device_map", "reduce_gpus"] = "device_map"

    @computed_field
    @property
    def hf_cache_dir(self) -> str:
        return os.path.abspath(os.path.join(_data_dir, self._hf_cache_dir))

    @computed_field
    @property
    def external_cache_dir(self) -> str:
        return os.path.abspath(os.path.join(_data_dir, self._external_cache_dir))

    @computed_field
    @property
    def script_dir(self) -> str:
        return os.path.abspath(os.path.join(_data_dir, self._script_dir))

    @computed_field
    @property
    def output_dir(self) -> str:
        return os.path.abspath(os.path.join(_data_dir, self._output_dir))

    @computed_field
    @property
    def worker_pid_file(self) -> str:
        return os.path.abspath(os.path.join(_data_dir, self._worker_pid_file))


class ModelConfig(BaseModel):
    id: str
    variant: str | None = None


class PreloadedModelsConfig(BaseModel):
    sd_base: Optional[List[ModelConfig]] = None
    gpt_base: Optional[List[ModelConfig]] = None
    controlnet: Optional[List[ModelConfig]] = None
    lora: Optional[List[ModelConfig]] = None


class ProxyConfig(BaseModel):
    host: str = ""
    port: int = 8080
    username: str = ""
    password: str = ""


class TaskErrorReportConfig(BaseModel):
    automatic: bool = False


class Config(BaseSettings):
    log: LogConfig

    ethereum: Ethereum

    db: DBConfig
    relay_url: str

    task_config: TaskConfig
    task_error_report: TaskErrorReportConfig = Field(
        default_factory=TaskErrorReportConfig
    )

    server_host: str = "0.0.0.0"
    server_port: int = 7412
    web_dist: str = ""

    resource_dir: str = ""

    staking_amount: Optional[int] = None

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            YamlConfigSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


_config: Optional[Config] = None


def get_config():
    global _config

    if _config is None:
        _config = Config()  # type: ignore

    return _config


def set_config(config: Config):
    global _config
    _config = config


_condition: Optional[Condition] = None


def _get_condition() -> Condition:
    global _condition

    if _condition is None:
        _condition = Condition()

    return _condition


async def wait_privkey() -> str:
    config = get_config()
    condition = _get_condition()
    async with condition:
        while len(config.ethereum.privkey) == 0:
            await condition.wait()
        return config.ethereum.privkey


async def set_privkey(privkey: str):
    config = get_config()
    condition = _get_condition()
    async with condition:
        config.ethereum.privkey = privkey
        condition.notify_all()

    await to_thread.run_sync(partial(config.ethereum.dump_privkey, privkey=privkey))


def get_privkey() -> str:
    config = get_config()
    return config.ethereum.privkey


def get_staking_amount() -> int:
    config = get_config()
    if config.staking_amount is None:
        raise ValueError("Staking amount has not been initialized.")
    return config.staking_amount


def ensure_staking_amount(min_staking_amount_wei: int) -> int:
    config = get_config()
    if config.staking_amount is None:
        amount = (min_staking_amount_wei + ETHER_WEI - 1) // ETHER_WEI
        set_staking_amount(amount)
    return get_staking_amount()


def set_staking_amount(amount: int):
    config = get_config()
    config.staking_amount = amount

    config_file = config_file_path()

    with open(config_file, mode="r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(r"staking_amount: (\d+)")
    match = pattern.search(content)
    if match:
        content = pattern.sub(f"staking_amount: {amount}", content)
    else:
        content += f"\nstaking_amount: {amount}"

    with open(config_file, mode="w", encoding="utf-8") as f:
        f.write(content)


_config_write_lock = threading.Lock()


def _write_yaml_value(section: str, key: str, value: Any):
    config_file = config_file_path()
    with _config_write_lock:
        with open(config_file, mode="r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        section_data = data.setdefault(section, {})
        if not isinstance(section_data, dict):
            raise ValueError(f"Config section {section} must be a mapping")
        section_data[key] = value

        dirname = os.path.dirname(config_file)
        fd, tmp_filename = tempfile.mkstemp(
            dir=dirname, prefix=".config.yml.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, mode="w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_filename, config_file)
        finally:
            if os.path.exists(tmp_filename):
                os.remove(tmp_filename)


def set_task_error_report_automatic(automatic: bool):
    config = get_config()
    _write_yaml_value("task_error_report", "automatic", automatic)
    config.task_error_report.automatic = automatic


class TxOption(TypedDict, total=False):
    chainId: int
    gas: int
    gasPrice: Wei
    maxFeePerGas: Wei
    maxPriorityFeePerGas: Wei


def get_default_tx_option() -> TxOption:
    config = get_config()
    res: TxOption = {}

    if config.ethereum.chain_id is not None:
        res["chainId"] = config.ethereum.chain_id
    if config.ethereum.gas is not None:
        res["gas"] = config.ethereum.gas
    if config.ethereum.gas_price is not None:
        res["gasPrice"] = Web3.to_wei(config.ethereum.gas_price, "wei")
    if config.ethereum.max_fee_per_gas is not None:
        res["maxFeePerGas"] = Web3.to_wei(config.ethereum.max_fee_per_gas, "wei")
    if config.ethereum.max_priority_fee_per_gas is not None:
        res["maxPriorityFeePerGas"] = Web3.to_wei(
            config.ethereum.max_priority_fee_per_gas, "wei"
        )
    return res


def get_requests_proxy_url(proxy: ProxyConfig | None) -> str | None:
    if proxy is not None and proxy.host != "":
        if "://" in proxy.host:
            scheme, host = proxy.host.split("://", 2)
        else:
            scheme, host = "", proxy.host

        proxy_str = ""
        if scheme != "":
            proxy_str += f"{scheme}://"

        if proxy.username != "":
            proxy_str += f"{proxy.username}"

            if proxy.password != "":
                proxy_str += f":{proxy.password}"

            proxy_str += f"@"

        proxy_str += f"{host}:{proxy.port}"

        return proxy_str
    else:
        return None


@contextmanager
def with_proxy(config: Config | None = None):
    if config is None:
        config = get_config()
    proxy_url = get_requests_proxy_url(config.task_config.proxy)
    if proxy_url is not None:
        origin_http_proxy = os.environ.get("HTTP_PROXY", None)
        origin_https_proxy = os.environ.get("HTTPS_PROXY", None)
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        try:
            yield
        finally:
            if origin_http_proxy is not None:
                os.environ["HTTP_PROXY"] = origin_http_proxy
            else:
                os.environ.pop("HTTP_PROXY")
            if origin_https_proxy is not None:
                os.environ["HTTPS_PROXY"] = origin_https_proxy
            else:
                os.environ.pop("HTTPS_PROXY")
    else:
        yield
