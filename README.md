# Physics Toy Factory

Physics Toy Factory is the product-side companion to S17Code. It owns the trusted p5.js fixture,
server-side smoke checker, dedicated scratch-workspace lifecycle, product API, and browser UI. Phase 2
adds the process-local session and S17 orchestration. Phase 3 adds the responsive workshop UI and real
journal-backed activity. Phase 4 adds the exact-hash preview gate, nonce-protected trusted shell,
sandboxed iframe, browser-error capture, and readiness watchdog.

Phase 5 adds the single linked follow-up control, narrower edit-only authority, fresh recorded
read/edit/check activity, and verified preview revision replacement.

Phase 6 adds the reproducible live qualification runbook, exact two-step demo and suggested-prompt
qualification tooling, a fail-closed genuine red-to-green repair proof, reviewed evidence
publication, and browser screenshot capture.

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 20 or newer available as `node`
- Playwright Chromium for the Phase 3 through Phase 6 browser gates
- Docker for live generated-code qualification in the pinned non-root Node image

Node is an external runtime prerequisite. There is intentionally no npm project, JavaScript package
manager, frontend build, or runtime CDN dependency.

## Setup

```bash
cp .env.example .env
uv sync --locked --dev
uv run playwright install chromium
uv run physics-toy-factory
```

The server binds to `127.0.0.1:8120` by default. On first startup the app copies its immutable seed to
`PTF_WORKSPACE`, initializes that directory as a dedicated Git repository, and records the base
fixture as `physics-toy-base-v1`. Start the S17 service with the product profile below before creating
a run.

The browser-facing API is:

```text
GET  /api/health
GET  /api/session
POST /api/session/reset
POST /api/runs
POST /api/runs/follow-up
GET  /api/runs/{session-owned-run-id}
GET  /api/runs/{session-owned-run-id}/events
GET  /api/code
POST /api/preview
GET  /preview/{verified-sha256}?preview_id={server-issued-id}
GET  /api/preview/p5.min.js?revision={verified-sha256}&preview_id={server-issued-id}
GET  /api/preview/sketch.js?revision={verified-sha256}&preview_id={server-issued-id}
POST /api/runs/{session-owned-run-id}/browser-error
```

The browser never supplies a workspace path or upstream URL, and it never receives the S17 control
token. A process restart intentionally forgets product session links. If the workspace contains a
prior sketch or other dirty content, the new session starts in `reset_required`; reset explicitly
before creating another toy.

## Phase 1 deterministic gate

```bash
uv sync --locked --dev
uv run ruff check .
uv run pytest -q tests/test_config.py tests/test_workspace.py tests/test_p5check.py
```

## Phase 2 deterministic gate

```bash
uv run ruff check .
uv run pytest -q tests/test_s17_client.py tests/test_orchestrator.py tests/test_api.py
```

These tests use an in-process fake S17 ASGI service and recorded graph/SSE fixtures. They require no
network or model call.

## Phase 3 deterministic gate

```bash
uv run ruff check .
uv run pytest -q tests/test_api.py
uv run pytest -q -m "browser and activity" tests/test_browser.py
```

The browser tests start the real product HTTP surface with an in-process fake S17 service and drive
one installed Chromium project. The recorded journey proves planning, write, checker failure, repair,
checker success, provenance, safe dialogs, and reconnect snapshot deduplication. Browser installation
is explicit and is never performed by the test suite.

The complete non-browser suite is:

```bash
uv run pytest -q -m "not browser"
```

If Node is absent, checker tests report an explicit skip for local diagnosis. Such a skip does not
satisfy the Phase 1 gate or CI.

## Phase 4 deterministic gate

```bash
uv run ruff check .
uv run pytest -q tests/test_api.py tests/test_workspace.py
uv run pytest -q -m "browser and preview" tests/test_browser.py
```

The preview browser journey proves that the iframe is created only for the current passing SHA,
uses exactly `sandbox="allow-scripts"` and `referrerpolicy="no-referrer"`, and becomes interactive
only after the trusted shell reports two animation frames. It also verifies that network, storage,
parent-DOM, popup, and top-navigation attempts fail; wrong-window and wrong-preview-ID messages are
ignored; a genuine runtime error is recorded without a repair run; and an unresponsive iframe is
destroyed by the configured watchdog.

## Phase 5 deterministic gate

```bash
uv run ruff check .
uv run pytest -q -m "not browser"
uv run pytest -q -m browser tests/test_browser.py
```

The linked follow-up journey proves that the old preview is removed before modification, the new
S17 run is linked to the preceding successful run, and its authority contains exactly `edit_code`
and `run_command`. The fake raw graph records `read_code` before one anchored `edit_code` result,
then a passing checker. The original interaction remains in the resulting source and preview, a new
verified revision replaces the old iframe, and a second follow-up is rejected without starting S17.

## Phase 6 final gate

```bash
# In S17Code
uv sync --locked --dev
uv run ruff check .
uv run pytest -q
uv run python proofs/p2_budget_holds.py --offline --task "summarise the attached notes" --budget 0.01
uv run python proofs/p3_denial_of_wallet.py --offline --task "keep refining the draft" --budget 0.01
uv run python proofs/p4_trace_export.py --offline --task "compare two options" --budget 0.01

# In PhysicsToyFactory
uv sync --locked --dev
uv run ruff check .
uv run pytest -q -m "not browser"
uv run pytest -q -m browser
```

Live qualification is deliberately separate from deterministic CI. Follow
[`docs/PHASE6_RUNBOOK.md`](docs/PHASE6_RUNBOOK.md) to launch the exact shared workspace and
container profile, exercise all four suggested prompts and the two-step solar-system demo, retain a
real red-to-green repair journal, and capture the final verified preview. The tooling fails if a run
does not converge; it never turns a first-attempt pass or fabricated event into repair evidence.

The retained 2026-08-15 evidence records a passed genuine repair proof but a failed product
qualification: all five create graphs finished without `answer_with_evidence`, the linked follow-up
was therefore rejected with HTTP 409, and no verified preview screenshot was captured. Deterministic
tests are green, but the live Phase 6 release gate and the MVP qualification remain incomplete.

Product runs include the validated `PTF_S17_RUN_BUDGET_USD` ceiling (default `$0.50`) and a stable
demo principal. This keeps creation and follow-up on S17's configured, metered model ladder rather
than its unbudgeted gateway default.

## Trusted fixture

The packaged fixture contains the workspace marker, `P5_API.md`, `p5check.js`, and the entire iframe
shell. `trusted_assets.json` records the size and SHA-256 of every required trusted asset. p5.js
`2.3.1` is vendored from the official versioned distribution and is never fetched at runtime.

Reset is deliberately narrow: it validates the absolute real path, marker, Git repository, base tag,
and fixture hashes before running Git with the validated workspace as its explicit working directory.
It refuses source-repository roots, home, filesystem root, symlinks, and tampered fixtures.

## S17 launch profile

The interactive product path uses an S17Code process with the exact same real workspace path and this
product-specific profile:

```text
S17_CONTROL_TOKEN=<same private value as PTF_S17_CONTROL_TOKEN>
S17_SANDBOX_ROOT=
S17_SKILLS_DIR=
S17_WORKSPACE=<exact real path of PTF_WORKSPACE>
S17_ALLOWED_COMMANDS=node
S17_PROTECTED_PATHS=.physics-toy-workspace,P5_API.md,p5check.js,shell/**,tests/**,test/**,**/tests/**,**/test_*.py,**/*_test.py,conftest.py,**/conftest.py,pytest.ini,tox.ini,setup.cfg,pyproject.toml,.github/**
S17_MAX_REPEAT_FAILURES=3
S17_EXEC_CONTAINER=1
S17_EXEC_IMAGE=<pinned non-root Node image>
```

Keep the sandbox and skills variables present but empty. S17 loads its repository `.env` on import;
unsetting them would let dotenv restore generic capabilities outside the product workspace profile.

Expose `S17_EXEC_CONTAINER` and `S17_EXEC_IMAGE` to the product process as well so `/api/health` can
report the configured mode. That report is configuration visibility, not independent proof that the
container runtime is available. Without runtime verification, the product does not claim a secure
sandbox.

`vm` is not an OS security boundary. If S17 container execution with networking disabled is not
available, the system is development-only for trusted prompts and must not be described as securely
sandboxed.

## MVP boundary

Phase 6 supplies the final qualification gate for the specified MVP; the MVP is complete only when
that live gate passes. It does not add automatic browser repair, Surprise Me, skill A/B,
planted-refusal demonstrations, persistent multi-user sessions, or extra follow-ups. A browser-only
failure remains display-only and never starts another S17 run.
