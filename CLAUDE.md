# CLAUDE.md

## Scope

This is the standalone Physics Toy Factory product repository. Keep S17 engine mechanics in the
sibling `S17Code` repository. Implement only the phase requested by the user; do not pull later-phase
orchestration or UI behavior forward.

## Commands

Use `uv` only. Never use bare `python`, `pytest`, or `pip`, and never run `ruff format`.

```bash
uv sync --locked --dev
uv run ruff check .
uv run pytest -q -m "not browser"
```

Phase 1's exact gate is:

```bash
uv run pytest -q tests/test_config.py tests/test_workspace.py tests/test_p5check.py
```

Node must be available for Phase 1. A skipped checker module is diagnostic only and is not a passing
phase gate.

## Invariants

- The browser never receives `PTF_S17_CONTROL_TOKEN` and never calls S17Code directly.
- Read environment variables once into the validated `Settings` object and inject it.
- Never accept a browser-provided filesystem path, upstream URL, or arbitrary run ID.
- Treat `workspace_seed`, `trusted_assets.json`, checker, marker, shell, tests, packaging, and CI as
  trusted product code. Generated `sketch.js` is untrusted.
- Validate the dedicated workspace identity and fixture hashes before start, reset, code read, or
  preview. Fail closed on ambiguity or tampering.
- Run subprocesses without a shell and with the validated workspace as explicit `cwd`.
- Never load p5.js or other runtime code from a CDN. Vendor reviewed, pinned assets with notices and
  hashes.
- Node `vm` is defense in depth, not an OS sandbox. Live generated code requires S17's pinned,
  non-root, no-network container execution.
- Render untrusted content as text, never HTML.
- Do not weaken the checker or workspace validation to make a test pass.

## Git

Use one reviewable branch per phase. Do not commit `.env`, `.runtime`, artifacts, browser output,
secrets, machine-specific absolute paths, or generated workspaces.
