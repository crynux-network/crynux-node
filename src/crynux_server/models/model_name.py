import json

from .task import TaskType


def normalize_model_name(name: str) -> str:
    """Lowercase a huggingface model name so that names differing only in
    letter case map to the same model. URL-based model names are kept
    unchanged because URL paths are case sensitive."""
    if name.startswith("http://") or name.startswith("https://"):
        return name
    return name.lower()


def _normalize_object_model_name(args: dict, object_key: str, name_key: str):
    obj = args.get(object_key)
    if isinstance(obj, dict):
        name = obj.get(name_key)
        if isinstance(name, str):
            obj[name_key] = normalize_model_name(name)


def _normalize_string_model_name(args: dict, key: str):
    name = args.get(key)
    if isinstance(name, str):
        args[key] = normalize_model_name(name)


def normalize_task_args_model_names(task_args: str, task_type: TaskType) -> str:
    """Rewrite the model name fields inside the task args json string with
    normalize_model_name, so that the model names used to load models during
    inference match the normalized model ids used for model downloading."""
    args = json.loads(task_args)
    if not isinstance(args, dict):
        return task_args

    if task_type == TaskType.SD:
        base_model = args.get("base_model")
        if isinstance(base_model, str):
            args["base_model"] = normalize_model_name(base_model)
        else:
            _normalize_object_model_name(args, "base_model", "name")
        _normalize_object_model_name(args, "lora", "model")
        _normalize_object_model_name(args, "controlnet", "model")
        _normalize_object_model_name(args, "refiner", "model")
        _normalize_string_model_name(args, "unet")
        _normalize_string_model_name(args, "vae")
        _normalize_string_model_name(args, "textual_inversion")
    elif task_type == TaskType.LLM:
        model = args.get("model")
        if isinstance(model, str):
            args["model"] = normalize_model_name(model)
    elif task_type == TaskType.SD_FT_LORA:
        _normalize_object_model_name(args, "model", "name")

    return json.dumps(args)
