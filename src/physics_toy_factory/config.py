"""Validated, process-wide product configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

ENV_TO_FIELD = {
    "PTF_HOST": "host",
    "PTF_PORT": "port",
    "PTF_S17_BASE_URL": "s17_base_url",
    "PTF_S17_CONTROL_TOKEN": "s17_control_token",
    "PTF_S17_RUN_BUDGET_USD": "s17_run_budget_usd",
    "PTF_WORKSPACE": "workspace",
    "PTF_ARTIFACT_DIR": "artifact_dir",
    "PTF_MAX_PROMPT_CHARS": "max_prompt_chars",
    "PTF_HTTP_CONNECT_TIMEOUT_SECONDS": "http_connect_timeout_seconds",
    "PTF_HTTP_READ_TIMEOUT_SECONDS": "http_read_timeout_seconds",
    "PTF_PREVIEW_READY_TIMEOUT_SECONDS": "preview_ready_timeout_seconds",
    "PTF_MAX_SKETCH_BYTES": "max_sketch_bytes",
    "S17_EXEC_CONTAINER": "s17_exec_container",
    "S17_EXEC_IMAGE": "s17_exec_image",
}


class Settings(BaseModel):
    """Immutable configuration injected into product services."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    host: str = "127.0.0.1"
    port: int = Field(default=8120, ge=1, le=65535)
    s17_base_url: AnyHttpUrl = "http://127.0.0.1:8113"
    s17_control_token: SecretStr
    s17_run_budget_usd: float = Field(default=0.50, gt=0, allow_inf_nan=False)
    workspace: Path
    artifact_dir: Path
    max_prompt_chars: int = Field(default=4000, ge=1, le=100_000)
    http_connect_timeout_seconds: float = Field(default=3, gt=0, le=60)
    http_read_timeout_seconds: float = Field(default=30, gt=0, le=600)
    preview_ready_timeout_seconds: float = Field(default=8, gt=0, le=60)
    max_sketch_bytes: int = Field(default=100_000, ge=1, le=1_000_000)
    s17_exec_container: bool = False
    s17_exec_image: str | None = None

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("PTF_HOST must bind to a loopback address")
        return value

    @field_validator("s17_control_token")
    @classmethod
    def validate_control_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value().strip()
        if not token or token == "replace-with-the-private-s17-control-token":
            raise ValueError("PTF_S17_CONTROL_TOKEN must be a non-placeholder private value")
        return SecretStr(token)

    @field_validator("workspace", "artifact_dir")
    @classmethod
    def validate_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("path must be absolute and must not use ~ or an environment placeholder")
        return value

    @model_validator(mode="after")
    def validate_storage_separation(self) -> Settings:
        workspace = self.workspace.resolve(strict=False)
        artifacts = self.artifact_dir.resolve(strict=False)
        if workspace == artifacts or artifacts.is_relative_to(workspace) or workspace.is_relative_to(artifacts):
            raise ValueError("PTF_WORKSPACE and PTF_ARTIFACT_DIR must be separate directory trees")
        if self.s17_exec_container:
            image = (self.s17_exec_image or "").strip()
            if not image or image.endswith(":latest"):
                raise ValueError("S17_EXEC_IMAGE must identify a pinned non-latest image in container mode")
        return self


def load_settings(
    *, env_file: Path | None = Path(".env"), environ: Mapping[str, str] | None = None
) -> Settings:
    """Read dotenv/environment values once and return the validated settings object."""

    raw: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        raw.update({key: value for key, value in dotenv_values(env_file).items() if value is not None})
    raw.update(dict(os.environ if environ is None else environ))
    values = {field: raw[key] for key, field in ENV_TO_FIELD.items() if key in raw}
    return Settings.model_validate(values)
