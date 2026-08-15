"""Configuration validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from physics_toy_factory.config import Settings, load_settings


def valid_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "PTF_S17_CONTROL_TOKEN": "test-private-control-token",
        "PTF_WORKSPACE": str(tmp_path / "workspace"),
        "PTF_ARTIFACT_DIR": str(tmp_path / "artifacts"),
    }


def test_load_settings_applies_locked_defaults(tmp_path: Path) -> None:
    settings = load_settings(env_file=None, environ=valid_environment(tmp_path))

    assert settings.host == "127.0.0.1"
    assert settings.port == 8120
    assert str(settings.s17_base_url) == "http://127.0.0.1:8113/"
    assert settings.max_prompt_chars == 4000
    assert settings.http_connect_timeout_seconds == 3
    assert settings.http_read_timeout_seconds == 30
    assert settings.preview_ready_timeout_seconds == 8
    assert settings.max_sketch_bytes == 100_000


def test_environment_overrides_dotenv_once(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PTF_PORT=9000",
                "PTF_S17_CONTROL_TOKEN=dotenv-token",
                f"PTF_WORKSPACE={tmp_path / 'dotenv-workspace'}",
                f"PTF_ARTIFACT_DIR={tmp_path / 'dotenv-artifacts'}",
            ]
        ),
        encoding="utf-8",
    )
    environment = valid_environment(tmp_path)
    environment["PTF_PORT"] = "8121"

    settings = load_settings(env_file=env_file, environ=environment)

    assert settings.port == 8121
    assert settings.s17_control_token.get_secret_value() == "test-private-control-token"


@pytest.mark.parametrize("field", ["PTF_S17_CONTROL_TOKEN", "PTF_WORKSPACE", "PTF_ARTIFACT_DIR"])
def test_required_configuration_is_enforced(tmp_path: Path, field: str) -> None:
    environment = valid_environment(tmp_path)
    del environment[field]

    with pytest.raises(ValidationError):
        load_settings(env_file=None, environ=environment)


@pytest.mark.parametrize("path_key", ["PTF_WORKSPACE", "PTF_ARTIFACT_DIR"])
def test_paths_must_be_absolute(tmp_path: Path, path_key: str) -> None:
    environment = valid_environment(tmp_path)
    environment[path_key] = "relative/path"

    with pytest.raises(ValidationError, match="path must be absolute"):
        load_settings(env_file=None, environ=environment)


def test_placeholder_token_is_rejected(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment["PTF_S17_CONTROL_TOKEN"] = "replace-with-the-private-s17-control-token"

    with pytest.raises(ValidationError, match="non-placeholder"):
        load_settings(env_file=None, environ=environment)


def test_non_loopback_binding_is_rejected(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment["PTF_HOST"] = "0.0.0.0"

    with pytest.raises(ValidationError, match="loopback"):
        load_settings(env_file=None, environ=environment)


@pytest.mark.parametrize("artifact_suffix", ["workspace", "workspace/artifacts"])
def test_artifacts_cannot_overlap_resettable_workspace(tmp_path: Path, artifact_suffix: str) -> None:
    environment = valid_environment(tmp_path)
    environment["PTF_ARTIFACT_DIR"] = str(tmp_path / artifact_suffix)

    with pytest.raises(ValidationError, match="separate directory trees"):
        load_settings(env_file=None, environ=environment)


def test_settings_are_frozen_and_secret_repr_is_redacted(tmp_path: Path) -> None:
    settings = Settings(
        s17_control_token="super-secret-token",
        workspace=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
    )

    assert "super-secret-token" not in repr(settings)
    with pytest.raises(ValidationError):
        settings.port = 9000  # type: ignore[misc]


def test_container_mode_requires_a_pinned_image(tmp_path: Path) -> None:
    values = {
        "s17_control_token": "private",
        "workspace": tmp_path / "workspace",
        "artifact_dir": tmp_path / "artifacts",
        "s17_exec_container": True,
    }
    with pytest.raises(ValidationError, match="pinned non-latest"):
        Settings(**values)
    with pytest.raises(ValidationError, match="pinned non-latest"):
        Settings(**values, s17_exec_image="node:latest")
    configured = Settings(**values, s17_exec_image="node:22.20.0-alpine")
    assert configured.s17_exec_container is True
