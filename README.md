# Physics Toy Factory

Physics Toy Factory is the product-side companion to S17Code. It owns the trusted p5.js fixture,
server-side smoke checker, dedicated scratch-workspace lifecycle, product API, and browser UI. Phase 1
establishes the package, executable checker, and safe workspace boundary; orchestration and the full UI
arrive in later phases.

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 20 or newer available as `node`

Node is an external runtime prerequisite. There is intentionally no npm project, JavaScript package
manager, frontend build, or runtime CDN dependency.

## Setup

```bash
cp .env.example .env
uv sync --locked --dev
uv run physics-toy-factory
```

The server binds to `127.0.0.1:8120` by default. `GET /api/health` is the only product endpoint in
Phase 1. On first startup the app copies its immutable seed to `PTF_WORKSPACE`, initializes that
directory as a dedicated Git repository, and records the base fixture as `physics-toy-base-v1`.

## Phase 1 deterministic gate

```bash
uv sync --locked --dev
uv run ruff check .
uv run pytest -q tests/test_config.py tests/test_workspace.py tests/test_p5check.py
```

The complete non-browser suite is:

```bash
uv run pytest -q -m "not browser"
```

If Node is absent, checker tests report an explicit skip for local diagnosis. Such a skip does not
satisfy the Phase 1 gate or CI.

## Trusted fixture

The packaged fixture contains the workspace marker, `P5_API.md`, `p5check.js`, and the entire iframe
shell. `trusted_assets.json` records the size and SHA-256 of every required trusted asset. p5.js
`2.3.1` is vendored from the official versioned distribution and is never fetched at runtime.

Reset is deliberately narrow: it validates the absolute real path, marker, Git repository, base tag,
and fixture hashes before running Git with the validated workspace as its explicit working directory.
It refuses source-repository roots, home, filesystem root, symlinks, and tampered fixtures.

## S17 launch profile

The interactive product path is added in Phase 2. Its S17Code process must use the exact same real
workspace path and this product-specific profile:

```text
S17_CONTROL_TOKEN=<same private value as PTF_S17_CONTROL_TOKEN>
S17_WORKSPACE=<exact real path of PTF_WORKSPACE>
S17_ALLOWED_COMMANDS=node
S17_PROTECTED_PATHS=.physics-toy-workspace,P5_API.md,p5check.js,shell/**,tests/**,test/**,**/tests/**,**/test_*.py,**/*_test.py,conftest.py,**/conftest.py,pytest.ini,tox.ini,setup.cfg,pyproject.toml,.github/**
S17_MAX_REPEAT_FAILURES=3
S17_EXEC_CONTAINER=1
S17_EXEC_IMAGE=<pinned non-root Node image>
```

`vm` is not an OS security boundary. If S17 container execution with networking disabled is not
available, the system is development-only for trusted prompts and must not be described as securely
sandboxed.

## Phase boundary

Phase 1 does not start S17 runs, proxy SSE, decide run readiness, serve generated code, or render an
iframe preview. Modules for those later contracts exist only as typed package seams.
