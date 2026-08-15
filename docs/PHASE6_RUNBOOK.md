# Phase 6 live qualification runbook

This runbook qualifies the real three-process topology against one dedicated scratch workspace. Run
the deterministic gates first. Live model runs are a release/demo gate and are not CI dependencies.

## 1. Fix the source revisions

Record these before launching anything:

```bash
git -C <S17Code> rev-parse HEAD
git -C <glc_v5> rev-parse HEAD
git -C <PhysicsToyFactory> rev-parse HEAD
```

The qualifying S17 revision must include the durable asynchronous-start endpoint. Do not aim
`S17_WORKSPACE` at either source repository.

## 2. Create the private product environment

Copy `.env.example` to the ignored `.env` in PhysicsToyFactory. Use one private token in both the
product and S17 processes and two separate absolute runtime directories:

```dotenv
PTF_HOST=127.0.0.1
PTF_PORT=8120
PTF_S17_BASE_URL=http://127.0.0.1:8113
PTF_S17_CONTROL_TOKEN=<same-private-value-as-S17_CONTROL_TOKEN>
PTF_WORKSPACE=<absolute-dedicated-runtime-dir>/workspace
PTF_ARTIFACT_DIR=<absolute-separate-runtime-dir>/artifacts
PTF_MAX_PROMPT_CHARS=4000
PTF_HTTP_CONNECT_TIMEOUT_SECONDS=3
PTF_HTTP_READ_TIMEOUT_SECONDS=30
PTF_PREVIEW_READY_TIMEOUT_SECONDS=8
PTF_MAX_SKETCH_BYTES=100000
S17_EXEC_CONTAINER=1
S17_EXEC_IMAGE=physics-toy-factory-node:22.20.0-phase6
```

Never commit `.env`, `.runtime`, provider credentials, or the original artifact directory.

## 3. Start Docker and build the non-root judge image

```bash
docker build \
  -f containers/phase6-node.Dockerfile \
  -t physics-toy-factory-node:22.20.0-phase6 \
  .
docker run --rm physics-toy-factory-node:22.20.0-phase6 id -u
```

The second command must print a nonzero UID. `S17Code` additionally invokes this image with
`--network=none`, bounded CPU, memory, process count, timeout, and a fixed `/workspace` mount.

## 4. Start glc_v5 and verify its route

From `glc_v5`, load its ignored environment and start the gateway:

```bash
uv sync
uv run glc serve
curl -sS http://127.0.0.1:8111/healthz
curl -sS http://127.0.0.1:8111/readyz
```

Use the configured logical provider; do not silently switch routes after a failed run. The retained
graphs record the actual provider/model values reported by nodes.

## 5. Start S17 with the exact product profile

Start from S17Code after loading its ignored provider/control environment, then override the product
boundary in that terminal:

```bash
unset S17_SANDBOX_ROOT
unset S17_SKILLS_DIR
export S17_WORKSPACE=<exact-same-absolute-path-as-PTF_WORKSPACE>
export S17_ALLOWED_COMMANDS=node
export S17_PROTECTED_PATHS='.physics-toy-workspace,P5_API.md,p5check.js,shell/**,tests/**,test/**,**/tests/**,**/test_*.py,**/*_test.py,conftest.py,**/conftest.py,pytest.ini,tox.ini,setup.cfg,pyproject.toml,.github/**'
export S17_MAX_REPEAT_FAILURES=3
export S17_EXEC_CONTAINER=1
export S17_EXEC_IMAGE=physics-toy-factory-node:22.20.0-phase6
uv run s17code serve
```

Clearing the generic sandbox and Markdown-skill roots is part of the product profile. It removes
unrelated `read_file`, `write_file`, indexing, calendar, and `load_skill` choices so the planner reads
`P5_API.md` and `sketch.js` only through the coding workspace capabilities.

Verify both endpoints before spending on a run:

```bash
curl -sS http://127.0.0.1:8113/healthz
curl -sS http://127.0.0.1:8113/readyz
```

## 6. Start Physics Toy Factory

```bash
uv sync --locked --dev
uv run physics-toy-factory
curl -sS http://127.0.0.1:8120/api/health
```

Health must report a verified workspace, S17 process up, gateway ready, and container mode configured.
The product truthfully reports that configuration visibility is not independent runtime proof.

## 7. Retain a genuine red-to-green proof

The product must be running and idle. The script asks the product to prove that state and reset, then
resets through the identity-validating workspace manager, installs the package-owned broken fixture,
and starts one edit-only S17 run:

```bash
uv run python scripts/retain_repair_proof.py \
  --budget-usd 0.50 \
  --publish-dir docs/evidence/phase6/repair-proof
```

Success requires a real nonzero checker completion, a later one-occurrence anchored edit, and a later
zero checker that remains the latest checker. The ignored artifact directory receives the original
graph, event tape, hashes, run ID, model route, and timestamp. Selected evidence is path-sanitized and
secret-checked before publication.

## 8. Qualify all prompts and the exact two-step demo

This command performs one attempt for each suggested prompt, then creates the exact solar-system demo
and applies the one linked glowing-trails follow-up. It leaves that final verified revision ready for
browser observation:

```bash
uv run python scripts/qualify_live_product.py \
  --publish-dir docs/evidence/phase6/live-qualification
```

The six product runs are:

1. `Rain that avoids my mouse`
2. `Bouncy magnets`
3. `Angry solar system`
4. `Fish that follow my cursor`
5. `Create a tiny solar system.`
6. linked follow-up `Make the planets leave glowing trails.`

A model failure is recorded as a failure. Only an infrastructure or transport failure may receive the
single explicitly approved retry, and that retry must be documented rather than overwriting evidence.

## 9. Capture browser/manual evidence

With the final linked revision still ready:

```bash
uv run python scripts/capture_live_demo.py \
  --output docs/evidence/phase6/live-qualification/final-linked-preview.png \
  --summary <ignored-live-artifact-dir>/summary.json \
  --summary docs/evidence/phase6/live-qualification/summary.json
```

The browser command requires the ready state, sandboxed iframe, visible canvas, pointer interaction,
and absence of a product error banner before it writes the screenshot and updates both summaries.

## 10. Evidence review and recovery

Before committing selected evidence, search it for secrets and machine paths, inspect every outcome,
and confirm run IDs against the live S17 graph. The summary must keep deterministic test evidence,
live-model evidence, browser observation, and unresolved limitations distinct.

If a process restarts, do not reconnect an old product session. Restart S17 and the product, press
Reset, and repeat the affected qualification. S17 journals remain durable; reset never deletes them.
