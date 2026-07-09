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
