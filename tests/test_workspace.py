"""Dedicated workspace identity and reset tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from physics_toy_factory.config import Settings
from physics_toy_factory.workspace import BASE_TAG, WorkspaceManager, WorkspaceSafetyError

EXPECTED_FIXTURE_FILES = {
    ".physics-toy-workspace",
    "P5_API.md",
    "p5check.js",
    "shell/LICENSE.p5js.txt",
    "shell/THIRD_PARTY_NOTICES.md",
    "shell/index.html",
    "shell/p5.min.js",
}


def settings_for(tmp_path: Path, workspace: Path | None = None) -> Settings:
    return Settings(
        s17_control_token="test-private-token",
        workspace=workspace or tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
    )


def fixture_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def test_initialize_creates_exact_trusted_git_fixture(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    manager = WorkspaceManager(settings)

    report = manager.ensure_initialized()

    assert report.root == settings.workspace.resolve()
    assert report.asset_count == 6
    assert fixture_files(report.root) == EXPECTED_FIXTURE_FILES
    assert not (report.root / "sketch.js").exists()
    assert settings.artifact_dir.is_dir()
    tagged = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{BASE_TAG}^{{commit}}"],
        cwd=report.root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tagged == report.base_commit


def test_existing_valid_workspace_is_idempotent(tmp_path: Path) -> None:
    manager = WorkspaceManager(settings_for(tmp_path))
    first = manager.ensure_initialized()
    second = manager.ensure_initialized()

    assert first == second


def test_reset_restores_exact_base_without_touching_artifacts(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    manager = WorkspaceManager(settings)
    report = manager.ensure_initialized()
    journal = settings.artifact_dir / "journal-sentinel.jsonl"
    journal.write_text("append-only evidence\n", encoding="utf-8")
    (report.root / "sketch.js").write_text("function setup() {}\n", encoding="utf-8")
    (report.root / "generated.tmp").write_text("untracked\n", encoding="utf-8")

    reset_report = manager.reset(idle=True)

    assert reset_report.base_commit == report.base_commit
    assert fixture_files(report.root) == EXPECTED_FIXTURE_FILES
    assert not (report.root / "sketch.js").exists()
    assert journal.read_text(encoding="utf-8") == "append-only evidence\n"


@pytest.mark.parametrize("asset", ["p5check.js", "P5_API.md", "shell/index.html", "shell/p5.min.js"])
def test_tampered_trusted_asset_fails_closed(tmp_path: Path, asset: str) -> None:
    manager = WorkspaceManager(settings_for(tmp_path))
    report = manager.ensure_initialized()
    (report.root / asset).write_bytes((report.root / asset).read_bytes() + b"tampered")

    with pytest.raises(WorkspaceSafetyError, match="hash mismatch"):
        manager.validate_identity()
    with pytest.raises(WorkspaceSafetyError, match="hash mismatch"):
        manager.reset(idle=True)


def test_missing_marker_refuses_reset_without_deleting_files(tmp_path: Path) -> None:
    workspace = tmp_path / "not-a-workspace"
    workspace.mkdir()
    victim = workspace / "keep.txt"
    victim.write_text("keep me", encoding="utf-8")
    manager = WorkspaceManager(settings_for(tmp_path, workspace))

    with pytest.raises(WorkspaceSafetyError, match="marker"):
        manager.reset(idle=True)

    assert victim.read_text(encoding="utf-8") == "keep me"


def test_reset_refuses_active_run_without_filesystem_change(tmp_path: Path) -> None:
    manager = WorkspaceManager(settings_for(tmp_path))
    report = manager.ensure_initialized()
    sketch_path = report.root / "sketch.js"
    sketch_path.write_text("active run output\n", encoding="utf-8")

    with pytest.raises(WorkspaceSafetyError, match="run is active"):
        manager.reset(idle=False)

    assert sketch_path.read_text(encoding="utf-8") == "active run output\n"


def test_workspace_must_be_a_git_repository(tmp_path: Path) -> None:
    workspace = tmp_path / "not-git"
    workspace.mkdir()
    (workspace / ".physics-toy-workspace").write_text(
        "physics-toy-factory-workspace\nschema-version=1\n", encoding="utf-8"
    )

    with pytest.raises(WorkspaceSafetyError, match="Git repository"):
        WorkspaceManager(settings_for(tmp_path, workspace)).validate_identity()


def test_symlink_workspace_is_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    link = tmp_path / "workspace-link"
    link.symlink_to(destination, target_is_directory=True)

    with pytest.raises(WorkspaceSafetyError, match="symlink"):
        WorkspaceManager(settings_for(tmp_path, link)).ensure_initialized()


def test_source_repository_root_is_rejected(tmp_path: Path) -> None:
    source_root = tmp_path / "source-repository"
    source_root.mkdir()
    manager = WorkspaceManager(settings_for(tmp_path, source_root), forbidden_roots=(source_root,))

    with pytest.raises(WorkspaceSafetyError, match="forbidden root"):
        manager.ensure_initialized()


def test_home_and_filesystem_root_are_rejected(tmp_path: Path) -> None:
    for unsafe in (Path.home(), Path("/")):
        unsafe_settings = Settings.model_construct(
            host="127.0.0.1",
            port=8120,
            s17_base_url="http://127.0.0.1:8113",
            s17_control_token="test-private-token",
            workspace=unsafe,
            artifact_dir=tmp_path / "artifacts",
            max_prompt_chars=4000,
            http_connect_timeout_seconds=3,
            http_read_timeout_seconds=30,
            preview_ready_timeout_seconds=8,
            max_sketch_bytes=100_000,
        )
        with pytest.raises(WorkspaceSafetyError, match="forbidden root"):
            WorkspaceManager(unsafe_settings).ensure_initialized()


def test_manifest_declares_pinned_local_p5_asset() -> None:
    manifest_path = (
        Path(__file__).parents[1] / "src" / "physics_toy_factory" / "trusted_assets.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = manifest["assets"]["shell/p5.min.js"]

    assert metadata["version"] == "2.3.1"
    assert metadata["upstream_url"] == "https://cdn.jsdelivr.net/npm/p5@2.3.1/lib/p5.min.js"
    assert metadata["license"] == "LGPL-2.1"
    assert "cdn.jsdelivr.net" not in (
        Path(__file__).parents[1] / "src" / "physics_toy_factory" / "workspace_seed" / "shell" / "index.html"
    ).read_text(encoding="utf-8")
