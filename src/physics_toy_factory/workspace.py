"""Creation, identity validation, and reset for the dedicated scratch workspace."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from physics_toy_factory.config import Settings

MARKER_CONTENT = "physics-toy-factory-workspace\nschema-version=1\n"
BASE_TAG = "physics-toy-base-v1"
PACKAGE_NAME = "physics_toy_factory"
MANIFEST_NAME = "trusted_assets.json"


class WorkspaceSafetyError(RuntimeError):
    """Raised when the workspace identity or trusted fixture is unsafe."""


@dataclass(frozen=True)
class WorkspaceReport:
    """Verified workspace identity returned to callers."""

    root: Path
    base_commit: str
    asset_count: int


class WorkspaceManager:
    """Own the exact dedicated Git workspace used by S17Code."""

    def __init__(self, settings: Settings, *, forbidden_roots: tuple[Path, ...] = ()) -> None:
        self._settings = settings
        product_root = Path(__file__).resolve().parents[2]
        engine_root = product_root.parent / "S17Code"
        self._forbidden_roots = (product_root, engine_root, *forbidden_roots)

    @property
    def root(self) -> Path:
        return self._settings.workspace

    def ensure_initialized(self) -> WorkspaceReport:
        """Create the immutable base fixture once, otherwise validate the existing workspace."""

        if self.root.exists() or self.root.is_symlink():
            report = self.validate_identity()
            self._settings.artifact_dir.mkdir(parents=True, exist_ok=True)
            return report
        root = self._validate_target_shape(require_exists=False)
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            with resources.as_file(resources.files(PACKAGE_NAME).joinpath("workspace_seed")) as seed:
                shutil.copytree(seed, root)
            self._initialize_git(root)
            self._settings.artifact_dir.mkdir(parents=True, exist_ok=True)
            return self.validate_identity()
        except Exception:
            if root.exists() and not (root / ".git").exists():
                shutil.rmtree(root)
            raise

    def validate_identity(self) -> WorkspaceReport:
        """Fail closed unless the workspace and all manifested assets are trusted."""

        root = self._validate_target_shape(require_exists=True)
        marker = root / ".physics-toy-workspace"
        if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_CONTENT:
            raise WorkspaceSafetyError("workspace marker is missing or invalid")
        if not (root / ".git").is_dir():
            raise WorkspaceSafetyError("workspace is not a dedicated Git repository")

        manifest = self._load_manifest()
        assets = manifest.get("assets")
        if not isinstance(assets, dict) or not assets:
            raise WorkspaceSafetyError("trusted asset manifest is invalid")
        for relative_path, metadata in assets.items():
            self._verify_asset(root, relative_path, metadata)

        try:
            base_commit = self._git(root, "rev-parse", "--verify", f"refs/tags/{BASE_TAG}^{{commit}}")
        except WorkspaceSafetyError as exc:
            raise WorkspaceSafetyError("workspace base tag is missing") from exc
        return WorkspaceReport(root=root, base_commit=base_commit, asset_count=len(assets))

    def reset(self, *, idle: bool) -> WorkspaceReport:
        """Restore the exact base fixture and remove every generated or ignored file."""

        if not idle:
            raise WorkspaceSafetyError("workspace reset is forbidden while a run is active")
        report = self.validate_identity()
        self._git(report.root, "reset", "--hard", report.base_commit)
        self._git(report.root, "clean", "-ffdx")
        verified = self.validate_identity()
        if (verified.root / "sketch.js").exists():
            raise WorkspaceSafetyError("reset left sketch.js behind")
        if self._git(verified.root, "status", "--porcelain"):
            raise WorkspaceSafetyError("reset left tracked or untracked changes behind")
        return verified

    def _validate_target_shape(self, *, require_exists: bool) -> Path:
        configured = self.root
        if not configured.is_absolute():
            raise WorkspaceSafetyError("workspace path must be absolute")
        if self._has_symlink_component(configured):
            raise WorkspaceSafetyError("workspace path must not contain a symlink")
        if require_exists and not configured.is_dir():
            raise WorkspaceSafetyError("workspace directory does not exist")
        resolved = configured.resolve(strict=require_exists)
        forbidden = {Path("/").resolve(), Path.home().resolve()}
        forbidden.update(path.resolve(strict=False) for path in self._forbidden_roots)
        if resolved in forbidden:
            raise WorkspaceSafetyError("workspace path targets a forbidden root")
        return resolved

    @staticmethod
    def _has_symlink_component(path: Path) -> bool:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                return True
            if not current.exists():
                break
        return False

    @staticmethod
    def _load_manifest() -> dict[str, Any]:
        try:
            text = resources.files(PACKAGE_NAME).joinpath(MANIFEST_NAME).read_text(encoding="utf-8")
            manifest = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceSafetyError("trusted asset manifest cannot be read") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise WorkspaceSafetyError("trusted asset manifest schema is invalid")
        return manifest

    @staticmethod
    def _verify_asset(root: Path, relative_path: object, metadata: object) -> None:
        if not isinstance(relative_path, str) or relative_path.startswith(("/", "../")):
            raise WorkspaceSafetyError("trusted asset path is invalid")
        if not isinstance(metadata, dict):
            raise WorkspaceSafetyError(f"trusted metadata is invalid for {relative_path}")
        asset = root / relative_path
        if not asset.is_file() or asset.is_symlink():
            raise WorkspaceSafetyError(f"trusted asset is missing: {relative_path}")
        payload = asset.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != metadata.get("size") or digest != metadata.get("sha256"):
            raise WorkspaceSafetyError(f"trusted asset hash mismatch: {relative_path}")

    @classmethod
    def _initialize_git(cls, root: Path) -> None:
        cls._git(root, "init", "-b", "main")
        cls._git(root, "config", "user.name", "Physics Toy Factory")
        cls._git(root, "config", "user.email", "physics-toy-factory@invalid.local")
        cls._git(root, "add", ".")
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        }
        cls._git(root, "commit", "-m", "Initialize trusted Physics Toy fixture", env=env)
        cls._git(root, "tag", BASE_TAG)

    @staticmethod
    def _git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceSafetyError("workspace Git operation failed") from exc
        if completed.returncode != 0:
            evidence = (completed.stderr or completed.stdout).strip().replace("\n", " ")[:300]
            raise WorkspaceSafetyError(f"workspace Git operation failed: {evidence}")
        return completed.stdout.strip()
