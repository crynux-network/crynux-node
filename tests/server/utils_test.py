from crynux_server import utils

NVIDIA_SMI_HEADER = (
    "index, uuid, name, utilization.gpu [%], memory.used [MiB], memory.total [MiB]"
)

SINGLE_GPU_OUTPUT = "\n".join(
    [
        NVIDIA_SMI_HEADER,
        "0, GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa, NVIDIA GeForce RTX 4090, 7 %, 1024 MiB, 24564 MiB",
    ]
)

DUAL_GPU_OUTPUT = "\n".join(
    [
        NVIDIA_SMI_HEADER,
        "0, GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa, NVIDIA GeForce RTX 4090, 7 %, 1024 MiB, 24564 MiB",
        "1, GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb, NVIDIA GeForce RTX 4090, 55 %, 2048 MiB, 24564 MiB",
    ]
)

MIXED_GPU_OUTPUT = "\n".join(
    [
        NVIDIA_SMI_HEADER,
        "0, GPU-cccccccc-cccc-cccc-cccc-cccccccccccc, NVIDIA GeForce RTX 3060, 90 %, 512 MiB, 12288 MiB",
        "1, GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa, NVIDIA GeForce RTX 4090, 7 %, 1024 MiB, 24564 MiB",
        "2, GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb, NVIDIA GeForce RTX 4090, 55 %, 2048 MiB, 24564 MiB",
    ]
)


def test_parse_nvidia_smi_gpus():
    cards = utils.parse_nvidia_smi_gpus(MIXED_GPU_OUTPUT)
    assert len(cards) == 3
    assert cards[0].index == 0
    assert cards[0].uuid == "GPU-cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert cards[0].name == "NVIDIA GeForce RTX 3060"
    assert cards[0].usage == 90
    assert cards[0].vram_used_mb == 512
    assert cards[0].vram_total_mb == 12288
    assert cards[2].index == 2
    assert cards[2].name == "NVIDIA GeForce RTX 4090"


def test_aggregate_single_gpu():
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(SINGLE_GPU_OUTPUT))
    assert info.model == "NVIDIA GeForce RTX 4090"
    assert info.usage == 7
    assert info.vram_used_mb == 1024
    assert info.vram_total_mb == 24564
    assert info.device_uuids == ["GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]


def test_aggregate_dual_identical_gpus():
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(DUAL_GPU_OUTPUT))
    assert info.model == "2x NVIDIA GeForce RTX 4090"
    assert info.usage == 55
    assert info.vram_used_mb == 3072
    assert info.vram_total_mb == 49128
    assert info.device_uuids == [
        "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    ]


def test_aggregate_mixed_gpus_selects_largest_group():
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(MIXED_GPU_OUTPUT))
    assert info.model == "2x NVIDIA GeForce RTX 4090"
    assert info.vram_total_mb == 49128
    assert info.device_uuids == [
        "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    ]


def test_group_size_tie_selects_larger_vram():
    output = "\n".join(
        [
            NVIDIA_SMI_HEADER,
            "0, GPU-dddddddd-dddd-dddd-dddd-dddddddddddd, NVIDIA GeForce RTX 4090, 7 %, 1024 MiB, 24564 MiB",
            "1, GPU-eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee, NVIDIA GeForce RTX 5090, 3 %, 512 MiB, 32607 MiB",
        ]
    )
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(output))
    assert info.model == "NVIDIA GeForce RTX 5090"
    assert info.vram_total_mb == 32607
    assert info.device_uuids == ["GPU-eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"]


def test_group_size_and_vram_tie_selects_lowest_index():
    output = "\n".join(
        [
            NVIDIA_SMI_HEADER,
            "0, GPU-ffffffff-ffff-ffff-ffff-ffffffffffff, NVIDIA A10, 3 %, 512 MiB, 23028 MiB",
            "1, GPU-99999999-9999-9999-9999-999999999999, NVIDIA A10G, 7 %, 1024 MiB, 23028 MiB",
        ]
    )
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(output))
    assert info.model == "NVIDIA A10"
    assert info.device_uuids == ["GPU-ffffffff-ffff-ffff-ffff-ffffffffffff"]


def test_resolve_gpt_executor_gating(monkeypatch):
    env_values = {}
    monkeypatch.setattr(utils, "load_env_file", lambda prefix: env_values)

    # TP by default on eligible platforms with >= 2 GPUs
    assert utils.resolve_gpt_executor("docker", 2) == "tensor_parallel"
    assert utils.resolve_gpt_executor("Linux", 2) == "tensor_parallel"
    assert utils.resolve_gpt_executor("docker", 4) == "tensor_parallel"

    # ineligible platforms
    assert utils.resolve_gpt_executor("Windows", 2) == "classic"
    assert utils.resolve_gpt_executor("Darwin", 2) == "classic"

    # single GPU or no GPU
    assert utils.resolve_gpt_executor("docker", 1) == "classic"
    assert utils.resolve_gpt_executor("Linux", 0) == "classic"

    # explicit env value tensor_parallel keeps the gate rules
    env_values["GPT_EXECUTOR"] = "tensor_parallel"
    assert utils.resolve_gpt_executor("docker", 2) == "tensor_parallel"
    assert utils.resolve_gpt_executor("Windows", 2) == "classic"
    assert utils.resolve_gpt_executor("docker", 1) == "classic"

    # classic opt-out wins everywhere
    env_values["GPT_EXECUTOR"] = "classic"
    assert utils.resolve_gpt_executor("docker", 2) == "classic"
    assert utils.resolve_gpt_executor("Linux", 4) == "classic"

    # any other value opts out
    env_values["GPT_EXECUTOR"] = "unknown"
    assert utils.resolve_gpt_executor("docker", 2) == "classic"


def test_gpu_name_tp_marker_dual_gpu(monkeypatch):
    monkeypatch.setattr(utils, "load_env_file", lambda prefix: {})
    monkeypatch.setattr(utils, "get_platform", lambda: "docker")
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(DUAL_GPU_OUTPUT))
    assert utils.apply_gpu_name_executor_marker(info) == "2x NVIDIA GeForce RTX 4090 TP"


def test_gpu_name_tp_marker_opt_out(monkeypatch):
    monkeypatch.setattr(
        utils, "load_env_file", lambda prefix: {"GPT_EXECUTOR": "classic"}
    )
    monkeypatch.setattr(utils, "get_platform", lambda: "docker")
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(DUAL_GPU_OUTPUT))
    assert utils.apply_gpu_name_executor_marker(info) == "2x NVIDIA GeForce RTX 4090"


def test_gpu_name_single_gpu_unchanged(monkeypatch):
    monkeypatch.setattr(utils, "load_env_file", lambda prefix: {})
    monkeypatch.setattr(utils, "get_platform", lambda: "docker")
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(SINGLE_GPU_OUTPUT))
    assert utils.apply_gpu_name_executor_marker(info) == "NVIDIA GeForce RTX 4090"


def test_gpu_name_windows_unchanged(monkeypatch):
    monkeypatch.setattr(utils, "load_env_file", lambda prefix: {})
    monkeypatch.setattr(utils, "get_platform", lambda: "Windows")
    info = utils.aggregate_gpu_info(utils.parse_nvidia_smi_gpus(DUAL_GPU_OUTPUT))
    assert utils.apply_gpu_name_executor_marker(info) == "2x NVIDIA GeForce RTX 4090"


async def test_gpu_info():
    gpu_info = await utils.get_gpu_info()
    assert len(gpu_info.model) > 0
    assert gpu_info.vram_total_mb > 0
    assert gpu_info.vram_used_mb > 0


async def test_cpu_info():
    cpu_info = await utils.get_cpu_info()
    if utils.get_os() == "Darwin":
        assert cpu_info.description
    elif utils.get_os() == "Linux":
        assert cpu_info.frequency_mhz > 0
    assert cpu_info.num_cores > 0
    assert cpu_info.usage > 0


async def test_memory_info():
    memory_info = await utils.get_memory_info()
    assert memory_info.available_mb > 0
    assert memory_info.total_mb > 0
    assert memory_info.available_mb < memory_info.total_mb


async def test_disk_info():
    disk_info = await utils.get_disk_info(
        "build/data/tmp/huggingface",
        "build/data/tmp/external",
        "build/data/logs",
        "build/data/tmp/results",
    )
    assert disk_info.hf_models >= 0
    assert disk_info.external_models >= 0
    assert disk_info.logs >= 0
    assert disk_info.temp_files >= 0
