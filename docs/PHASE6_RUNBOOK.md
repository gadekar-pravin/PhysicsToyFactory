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

The qualifying S17 revision must include the durable asynchronous-start endpoint, JSON-safe
`run_command` results, and local Unix Docker socket propagation. Do not aim `S17_WORKSPACE` at either
source repository.

### Pinned dependency revisions

| Repository | Pin | Supersedes |
| --- | --- | --- |
| S17Code | `7bf4b1e937699449a6a883e4862184559db1a91b` | `4e085cb2e869694f036df2a6171530e147077e85` |
| glc_v5 | `77054f4b7a4d9879d33c5221ff08a35fdf48eb10` | `66ed155addd78fe8f59673ddca59e0277a7d39e8` |

Both pins are the merge commits on `main`. The superseded S17 pin was the tip of the deleted
`phase6-container-command-result` branch; the branch was rebased before merge, so that commit is now
reachable from no local or remote ref and will disappear whenever `git gc` prunes it. Its content is
on `main` as `d0c2720`. `docs/evidence/phase6/EVIDENCE.md` records the superseded pins because they
are what the retained live runs actually used; do not edit that history.

### Qualification status of these pins

The retained live pass in `EVIDENCE.md` was produced at the superseded pins. At the current pins the
deterministic gates are green and the container checker path is verified, but **no live paid run has
been executed**. Treat §8 against these pins as a first qualification, not a re-confirmation, and
take the canary before the full suite.

The two revision deltas are small and were reviewed rather than assumed:

- glc_v5 `66ed155..77054f4` changes `.env.example` only — documentation, no runtime behaviour.
- S17Code `4e085cb..7bf4b1e` changes a README example, adds tests, and pins `num_ctx=512` on
  `nomic-embed-text` requests in `s17code/core/memory/embeddings.py`. That path is live during
  qualification: `runtime.py` builds the embedder for every run and `answer_with_evidence` writes an
  episode through it. The pin only moves Ollama's truncation point to one the runner survives;
  vectors for text that already fits are byte-identical, so the retained sketch and route evidence
  remain comparable.

### Reproduce the pins as detached worktrees

Qualify from worktrees rather than from the source checkouts, so `main` can move without disturbing a
qualification in progress:

```bash
git -C <S17Code> worktree add --detach <s17-worktree> 7bf4b1e937699449a6a883e4862184559db1a91b
git -C <glc_v5> worktree add --detach <glc-worktree> 77054f4b7a4d9879d33c5221ff08a35fdf48eb10
```

A detached worktree contains tracked files only. It carries no ignored `.env`, so each worktree
terminal must load its source repository's environment explicitly:

```bash
set -a; source <S17Code>/.env; set +a
```

This also changes where S17 looks for dotenv. `s17code/main.py` resolves `ROOT` from the running
module's own location, so a worktree process reads `<s17-worktree>/.env`, which does not exist. The
resurrection risk in §5 therefore comes entirely from the `source` above, and the §5 assertion must
name the source repository's `.env` explicitly.

## 2. Create the private product environment

Copy `.env.example` to the ignored `.env` in PhysicsToyFactory. Use one private token in both the
product and S17 processes and two separate absolute runtime directories:

```dotenv
PTF_HOST=127.0.0.1
PTF_PORT=8120
PTF_S17_BASE_URL=http://127.0.0.1:8113
PTF_S17_CONTROL_TOKEN=<same-private-value-as-S17_CONTROL_TOKEN>
PTF_S17_RUN_BUDGET_USD=0.50
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

### If the default ports are occupied

`8111`, `8113` and `8120` are the defaults everywhere in this runbook. A development stack often
already holds the first two. Relocating is supported — for example gateway `8211`, S17 `8213`,
product `8220` — but the port lives in four places that do not track each other:

- `PTF_PORT` and `PTF_S17_BASE_URL` in the product `.env`;
- `GLC_HOST` / `GLC_PORT` in the gateway terminal;
- `S17_PORT` and `GLC_BASE_URL` in the S17 terminal; and
- **`--product-base-url` on every script in §7 through §9**, which defaults to
  `http://127.0.0.1:8120` and will otherwise silently address the wrong process.

Relocated ports must be recorded in the evidence set alongside the revisions.

## 3. Start Docker and build the non-root judge image

```bash
docker build \
  -f containers/phase6-node.Dockerfile \
  -t physics-toy-factory-node:22.20.0-phase6 \
  .
docker run --rm physics-toy-factory-node:22.20.0-phase6 id -u
```

The second command must print `1000`, the image's `node` user, and never `0`. `S17Code` additionally
invokes this image with `--network=none`, bounded CPU, memory, process count, timeout, and a fixed
`/workspace` mount.

## 4. Start glc_v5 and verify its route

From the gateway worktree, load `glc_v5`'s ignored environment, point the ledger at a fresh
qualification-private database, and start the gateway:

```bash
set -a; source <glc_v5>/.env; set +a
export GLC_GATEWAY_DB=<runtime-dir>/gateway.sqlite
uv sync
uv run glc serve
curl -sS http://127.0.0.1:8111/healthz
curl -sS http://127.0.0.1:8111/readyz
```

`GLC_GATEWAY_DB` is not optional. Every GLC generation on a machine otherwise shares
`~/.glc/gateway.sqlite`, whose `calls` table was created by an older generation as an append-only
ledger with a `CHECK(schema_version = 2)` column that glc_v5 never writes. glc_v5 creates that table
with `CREATE TABLE IF NOT EXISTS`, so the create silently no-ops and every `POST /v1/chat` dies at
insert time — surfacing as a gateway 500 and a `failed` S17 run with zero nodes, long after
`/healthz` and `/readyz` have both passed. A qualification-private path also keeps the list-price
ledger scoped to this qualification's calls, which is what makes the published gateway total
comparable to the S17 controller total. Never migrate or drop `~/.glc/gateway.sqlite`; it is an audit
ledger.

Use the configured logical provider; do not silently switch routes after a failed run. The retained
graphs record the actual provider/model values reported by nodes.

## 5. Start S17 with the exact product profile

Start from the S17 worktree after loading S17Code's ignored provider/control environment, then
override the product boundary in that terminal:

```bash
set -a; source <S17Code>/.env; set +a
export S17_SANDBOX_ROOT=
export S17_SKILLS_DIR=
export S17_DATA_DIR=<runtime-dir>/s17-data
export S17_WORKSPACE=<exact-same-absolute-path-as-PTF_WORKSPACE>
export S17_ALLOWED_COMMANDS=node
export S17_PROTECTED_PATHS='.physics-toy-workspace,P5_API.md,p5check.js,shell/**,tests/**,test/**,**/tests/**,**/test_*.py,**/*_test.py,conftest.py,**/conftest.py,pytest.ini,tox.ini,setup.cfg,pyproject.toml,.github/**'
export S17_MAX_REPEAT_FAILURES=3
export S17_EXEC_CONTAINER=1
export S17_EXEC_IMAGE=physics-toy-factory-node:22.20.0-phase6
export DOCKER_HOST=unix://<absolute-path-to-the-local-docker-socket>
uv sync
uv run s17code serve
```

`S17_DATA_DIR` gives the qualification its own journal and memory database, so run IDs and spend in
the published evidence belong to this qualification alone.

`DOCKER_HOST` is required whenever the Docker daemon does not listen on `/var/run/docker.sock` —
which is the normal case for Docker Desktop on macOS, where the socket is under the user's
`~/.docker/run/`. `s17code/coding/exec.py` builds a deliberately scrubbed child environment
containing only `PATH`, `HOME`, `LANG` and `PYTHONDONTWRITEBYTECODE`, and forwards `DOCKER_HOST` only
when it is already set in the server's environment; it must be a local `unix://` socket or the run is
refused. With it unset, every checker invocation exits `125` with
`Cannot connect to the Docker daemon at unix:///var/run/docker.sock`, which is the defect that
stopped `run-e48a22aa0e57`. Confirm the socket path before starting:

```bash
docker context inspect --format '{{.Endpoints.docker.Host}}'
```

The explicit empty exports are intentional. `load_dotenv` never overrides a variable that is already
present, so an empty-but-present value wins while an `unset` one would be repopulated — from the
`source` above in the worktree flow, and additionally from S17's own import-time
`load_dotenv(ROOT / ".env")` when serving directly out of the S17Code checkout. This removes
unrelated `read_file`, `write_file`, indexing, calendar, and `load_skill` choices so the planner
reads `P5_API.md` and `sketch.js` only through the coding workspace capabilities.

Before starting the server, prove that dotenv cannot resurrect those roots in this terminal. Name the
source repository's `.env` explicitly — a relative `.env` resolves inside the worktree, where no such
file exists, and the assertion would then pass without testing anything:

```bash
uv run python -c 'import os,sys; from dotenv import load_dotenv; p=sys.argv[1]; assert os.path.exists(p), p; load_dotenv(p); assert os.getenv("S17_SANDBOX_ROOT") == ""; assert os.getenv("S17_SKILLS_DIR") == ""' <S17Code>/.env
```

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

Health must report a verified workspace, S17 process up, gateway ready, and container mode configured:

```text
.ready                          = true
.workspace.verified             = true
.s17.process.up                 = true
.s17.gateway.ready              = true
.container_mode.configured      = true
.container_mode.secure_sandbox_claimed = false
```

`secure_sandbox_claimed` is hard-coded `false` and stays `false`. The product truthfully reports that
configuration visibility is not independent runtime proof.

## 7. Retain a genuine red-to-green proof

Every script in this section and the two that follow addresses the product over HTTP and defaults to
`--product-base-url http://127.0.0.1:8120`. If §2 relocated the product port, pass the flag on each
invocation below; the commands as written would otherwise address whatever else holds `8120`.

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

Every run in this section is billable. `PTF_S17_RUN_BUDGET_USD` caps each run at `$0.50`, so the six
runs of a full qualification carry up to `$3.00` of configured controller allowance. That is a
ceiling, not an estimate: the 2026-08-16 requalification metered `$0.259` at the controller and
`$0.376` on the gateway's list-price ledger across all 59 calls. Provider billing is a third
accounting view and can differ from both.

After a configuration correction, obtain explicit approval for one paid canary before repeating the
full suite. This mode resets the product and starts exactly one solar creation; it never runs a
suggested prompt or follow-up:

```bash
uv run python scripts/qualify_live_product.py \
  --solar-canary-only \
  --publish-dir docs/evidence/phase6/canary-YYYY-MM-DD
```

Require a metered frontier planner route, succeeded `answer_with_evidence`, latest checker exit `0`,
verified sketch hash, and ready preview before authorizing broader qualification.

### Full qualification

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
