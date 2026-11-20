import json

from crynux_server.models import (
    ModelConfig,
    TaskType,
    normalize_model_name,
    normalize_task_args_model_names,
)


def test_normalize_model_name():
    assert normalize_model_name("Qwen/Qwen2.5-7B") == "qwen/qwen2.5-7b"
    url = "https://example.com/Models/MyLora.safetensors"
    assert normalize_model_name(url) == url


def test_model_config_normalizes_id():
    model = ModelConfig(id="Qwen/Qwen2.5-7B", type="base")
    assert model.id == "qwen/qwen2.5-7b"
    assert model.to_model_id() == "base:qwen/qwen2.5-7b"

    model = ModelConfig.from_model_id("base:Qwen/Qwen2.5-7B")
    assert model.id == "qwen/qwen2.5-7b"


def test_model_config_skips_component_model_ids():
    model_ids = [
        "base:Qwen/Qwen-Image-2.1",
        "lora:Org/Style",
        "component:transformer:abenzerps/Qwen-Image-2.1-Uncensored-Quantized",
        "controlnet:Lllyasviel/Canny",
    ]
    legacy = ModelConfig.from_legacy_model_ids(model_ids)
    assert [m.to_model_id() for m in legacy] == [
        "base:qwen/qwen-image-2.1",
        "lora:org/style",
        "controlnet:lllyasviel/canny",
    ]
    auxiliary = ModelConfig.from_auxiliary_model_ids(model_ids)
    assert [m.to_model_id() for m in auxiliary] == [
        "lora:org/style",
        "controlnet:lllyasviel/canny",
    ]


def test_normalize_task_args_model_names_llm():
    task_args = json.dumps(
        {
            "model": "Qwen/Qwen2.5-7B",
            "messages": [{"role": "user", "content": "Hi"}],
            "seed": 42,
        }
    )
    normalized = json.loads(normalize_task_args_model_names(task_args, TaskType.LLM))
    assert normalized["model"] == "qwen/qwen2.5-7b"
    assert normalized["messages"] == [{"role": "user", "content": "Hi"}]


def test_normalize_task_args_model_names_sd():
    task_args = json.dumps(
        {
            "base_model": {"name": "Crynux-Network/SDXL-Turbo", "variant": "fp16"},
            "prompt": "a cat",
            "lora": {"model": "https://example.com/Models/MyLora.safetensors"},
            "controlnet": {"model": "Lllyasviel/Sd-Controlnet-Canny"},
            "refiner": {"model": "StabilityAI/Stable-Diffusion-XL-Refiner-1.0"},
            "unet": "Crynux-Network/MyUNet",
            "vae": "MadeByOllin/SDXL-VAE-FP16-Fix",
            "textual_inversion": "SD-Concepts-Library/Cat-Toy",
            "task_config": {"num_images": 1},
        }
    )
    normalized = json.loads(normalize_task_args_model_names(task_args, TaskType.SD))
    assert normalized["base_model"]["name"] == "crynux-network/sdxl-turbo"
    assert normalized["base_model"]["variant"] == "fp16"
    assert normalized["lora"]["model"] == "https://example.com/Models/MyLora.safetensors"
    assert normalized["controlnet"]["model"] == "lllyasviel/sd-controlnet-canny"
    assert normalized["refiner"]["model"] == "stabilityai/stable-diffusion-xl-refiner-1.0"
    assert normalized["unet"] == "crynux-network/myunet"
    assert normalized["vae"] == "madebyollin/sdxl-vae-fp16-fix"
    assert normalized["textual_inversion"] == "sd-concepts-library/cat-toy"


def test_normalize_task_args_model_names_sd_string_base_model():
    task_args = json.dumps(
        {"base_model": "Crynux-Network/SDXL-Turbo", "prompt": "a cat"}
    )
    normalized = json.loads(normalize_task_args_model_names(task_args, TaskType.SD))
    assert normalized["base_model"] == "crynux-network/sdxl-turbo"


def test_normalize_task_args_model_names_sd_ft_lora():
    task_args = json.dumps(
        {
            "model": {"name": "Crynux-Network/Stable-Diffusion-v1-5", "variant": "fp16"},
            "dataset_name": "lambdalabs/naruto-blip-captions",
        }
    )
    normalized = json.loads(
        normalize_task_args_model_names(task_args, TaskType.SD_FT_LORA)
    )
    assert normalized["model"]["name"] == "crynux-network/stable-diffusion-v1-5"


def test_normalize_task_args_model_names_video_and_lora_model_id():
    task_args = json.dumps(
        {
            "version": "3.8.0",
            "base_model": {"name": "Wan-AI/Wan2.2-I2V-A14B-Diffusers"},
            "prompt": "Rain on window",
            "components": {
                "text_encoder": {
                    "format": "diffusers",
                    "repo": "Org/Custom-UMT5",
                    "format_kwargs": {"subfolder": "text_encoder"},
                }
            },
            "loras": [
                {
                    "model": "LightX2V/Wan2.2-Lightning",
                    "weight_file_name": "Wan2.2_High.safetensors",
                    "weight": 100,
                },
                {
                    "model": "https://example.com/Models/MotionLora.safetensors",
                    "weight": 80,
                },
            ],
        }
    )
    normalized = json.loads(
        normalize_task_args_model_names(task_args, TaskType.VIDEO)
    )
    assert normalized["base_model"]["name"] == "wan-ai/wan2.2-i2v-a14b-diffusers"
    assert normalized["components"]["text_encoder"]["repo"] == "org/custom-umt5"
    assert normalized["loras"][0]["model"] == "lightx2v/wan2.2-lightning"
    assert normalized["loras"][0]["weight_file_name"] == "Wan2.2_High.safetensors"
    assert (
        normalized["loras"][1]["model"]
        == "https://example.com/Models/MotionLora.safetensors"
    )

    lora_cfg = ModelConfig.from_model_id(
        "lora:LightX2V/Wan2.2-Lightning@deadbeef#Wan2.2_High.safetensors"
    )
    assert lora_cfg.id == "lightx2v/wan2.2-lightning"
    assert lora_cfg.revision == "deadbeef"
    assert lora_cfg.weight_file_name == "Wan2.2_High.safetensors"
    assert (
        lora_cfg.to_model_id()
        == "lora:lightx2v/wan2.2-lightning@deadbeef#Wan2.2_High.safetensors"
    )
