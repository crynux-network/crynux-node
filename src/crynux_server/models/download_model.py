from typing import Iterable, List, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .model_file import IMAGE_BASE_MODEL_FORMAT, ModelFileEntry
from .model_name import normalize_model_name
from .task import TaskType


def is_component_model_id(model_id: str) -> bool:
    return model_id.lower().startswith("component:")


def is_base_model_id(model_id: str) -> bool:
    return model_id.lower().startswith("base:")


class ModelConfig(BaseModel):
    id: str
    type: Literal["base", "lora", "controlnet"]
    variant: str | None = None
    revision: str | None = None
    weight_file_name: str = ""

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, v: str) -> str:
        return normalize_model_name(v)

    def to_model_id(self) -> str:
        if self.id.startswith(("http://", "https://")):
            return f"{self.type}:{self.id}"
        model_body = self.id
        if self.variant:
            model_body += f"+{self.variant}"
        if self.revision:
            model_body += f"@{self.revision}"
        if self.weight_file_name:
            model_body += f"#{self.weight_file_name}"
        return f"{self.type}:{model_body}"

    @classmethod
    def from_model_id(cls, model_id: str) -> "ModelConfig":
        model_type, model_body = model_id.split(":", maxsplit=1)
        if model_body.startswith(("http://", "https://")):
            return cls.model_validate({"id": model_body, "type": model_type})

        weight_file_name = ""
        if "#" in model_body:
            model_body, weight_file_name = model_body.split("#", maxsplit=1)
        revision: str | None = None
        if "@" in model_body:
            model_body, revision = model_body.split("@", maxsplit=1)
            revision = revision or None
        if "+" in model_body:
            model_name, variant = model_body.split("+", maxsplit=1)
        else:
            model_name = model_body
            variant = None
        return cls.model_validate(
            {
                "id": model_name,
                "type": model_type,
                "variant": variant,
                "revision": revision,
                "weight_file_name": weight_file_name,
            }
        )

    @classmethod
    def from_legacy_model_ids(cls, model_ids: Iterable[str]) -> List["ModelConfig"]:
        """Parse model_ids that use legacy ModelConfig types.

        Identifiers prefixed with ``component:`` are omitted. Components are
        distributed and loaded through ``execution_model_files``.
        """
        return [
            cls.from_model_id(model_id)
            for model_id in model_ids
            if not is_component_model_id(model_id)
        ]

    @classmethod
    def from_auxiliary_model_ids(cls, model_ids: Iterable[str]) -> List["ModelConfig"]:
        """Parse non-base legacy model_ids for task-scoped foreground download.

        ``component:`` identifiers are omitted; they are not downloaded on this
        path.
        """
        return [
            cls.from_model_id(model_id)
            for model_id in model_ids
            if not is_base_model_id(model_id) and not is_component_model_id(model_id)
        ]

    def to_model_file_entry(self, task_type: TaskType) -> ModelFileEntry:
        """Source entry of a base model; the entry carries no revision."""
        if self.type != "base":
            raise ValueError(f"Model type {self.type} has no model file entry")
        model_format = "" if task_type == TaskType.LLM else IMAGE_BASE_MODEL_FORMAT
        return ModelFileEntry(repository=self.id, format=model_format)


class DownloadedModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    task_type: TaskType
    model_file: ModelFileEntry

    @field_validator("model_file")
    @classmethod
    def _require_revision(cls, v: ModelFileEntry) -> ModelFileEntry:
        if not v.is_exact:
            raise ValueError("A downloaded model file requires a revision")
        return v
