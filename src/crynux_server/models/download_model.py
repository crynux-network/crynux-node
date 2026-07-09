from typing import List, Literal

from pydantic import BaseModel, field_validator

from .model_name import normalize_model_name
from .task import TaskType


class ModelConfig(BaseModel):
    id: str
    type: Literal["base", "lora", "controlnet"]
    variant: str | None = None

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, v: str) -> str:
        return normalize_model_name(v)

    def to_model_id(self) -> str:
        model_body = self.id
        if self.variant:
            model_body += f"+{self.variant}"
        return f"{self.type}:{model_body}"

    @classmethod
    def from_model_id(cls, model_id: str) -> "ModelConfig":
        model_type, model_body = model_id.split(":", maxsplit=1)
        if "+" in model_body:
            model_name, variant = model_body.split("+", maxsplit=1)
        else:
            model_name = model_body
            variant = None
        return cls.model_validate(
            {"id": model_name, "type": model_type, "variant": variant}
        )


class DownloadedModel(BaseModel):
    task_type: TaskType
    model: ModelConfig
