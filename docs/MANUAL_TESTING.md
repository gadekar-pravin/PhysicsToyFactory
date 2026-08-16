# Manual testing runbook

This runbook brings the real three-process topology up from nothing and drives it by hand in a
browser. It is for exploring behaviour, demonstrating the product, and diagnosing a problem you can
see on screen.

It is **not** the release gate. `PHASE6_RUNBOOK.md` is the scripted qualification that produces
retained evidence; nothing you do here is publishable evidence, because a hand-driven run records no
sanitized graph, no hash set, and no reviewed summary.

**Every build costs real money.** Each run is capped by `PTF_S17_RUN_BUDGET_USD` (default `$0.50`).
A typical creation spends `$0.03`–`$0.07`. There is no offline or dry-run mode for the product path:
the planner is a live frontier model.

## 0. Set your three paths once

Every command below reads these. Set them in each terminal you open, or put them in your shell
profile.

```bash
export PTF=<absolute-path-to-PhysicsToyFactory>
export S17=<absolute-path-to-S17Code>
export GLC=<absolute-path-to-glc_v5>
export RT="$PTF/.runtime/manual"      # all mutable state for manual testing lives here
```

`$RT` is inside the gitignored `.runtime/`. Deleting it resets manual testing completely.

## 1. Prerequisites

All four must be true before anything else. Each has a verification command; run them all.

| Requirement | Verify | Expected |
| --- | --- | --- |
| Docker daemon running | `docker info --format '{{.ServerVersion}}'` | a version, no error |
| Docker socket path known | `docker context inspect --format '{{.Endpoints.docker.Host}}'` | a `unix://…` path |
| Node available | `node --version` | v20 or newer |
| **Ollama with `nomic-embed-text`** | `curl -sS http://127.0.0.1:11434/api/tags` | JSON listing `nomic-embed-text` |

Ollama is not optional and its absence is easy to misdiagnose. `s17code/runtime.py` constructs
`OllamaNomicEmbedder()` unconditionally, with no deterministic fallback, and a successful run writes
its answer into memory through that embedder. With Ollama down, the planner appears to work and then
the run dies at the final step. Start it with `open -a Ollama` and pull `nomic-embed-text` if
missing.

You also need provider credentials in `$GLC/.env`. This repository holds none by design; the gateway
owns them all.

## 2. One-time setup

### Product environment

```bash
cd "$PTF"
cp .env.example .env
mkdir -p "$RT"
```

Edit `.env`. The values that matter:

```dotenv
PTF_PORT=8220
PTF_S17_BASE_URL=http://127.0.0.1:8213
PTF_S17_CONTROL_TOKEN=<the exact value of S17_CONTROL_TOKEN in S17Code/.env>
PTF_S17_RUN_BUDGET_USD=0.50
PTF_WORKSPACE=<absolute $RT>/workspace
PTF_ARTIFACT_DIR=<absolute $RT>/artifacts
S17_EXEC_CONTAINER=1
S17_EXEC_IMAGE=physics-toy-factory-node:22.20.0-phase6
```

`PTF_WORKSPACE` and `PTF_ARTIFACT_DIR` must be absolute; `$RT` will not be expanded from inside the
file. The control token must match `S17_CONTROL_TOKEN` character for character — a mismatch surfaces
as a product error the moment you press Build, not at startup. Copy it without printing it:

```bash
grep '^S17_CONTROL_TOKEN=' "$S17/.env" | sed 's/^S17_CONTROL_TOKEN=/PTF_S17_CONTROL_TOKEN=/' >> "$PTF/.env"
chmod 600 "$PTF/.env"
```

Never commit `.env`. It is gitignored; keep it that way.

### Checker image

```bash
cd "$PTF"
docker build -f containers/phase6-node.Dockerfile -t physics-toy-factory-node:22.20.0-phase6 .
docker run --rm physics-toy-factory-node:22.20.0-phase6 id -u
```

The last command must print `1000`. A `0` means the image would run generated code as root and must
not be used.

## 3. Ports

This runbook uses **gateway 8211, S17 8213, product 8220** so it never collides with a development
stack already holding the standard 8111/8113/8120. If those defaults are free and you prefer them,
change `PTF_PORT`, `PTF_S17_BASE_URL`, and the three exports below together — they must agree.

Check before starting:

```bash
for p in 8211 8213 8220; do lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1 && echo ":$p BUSY" || echo ":$p free"; done
```

## 4. Terminal 1 — gateway

```bash
cd "$GLC"
set -a; source "$GLC/.env"; set +a
export GLC_HOST=127.0.0.1
export GLC_PORT=8211
export GLC_GATEWAY_DB="$RT/gateway.sqlite"
uv sync
uv run glc serve
```

`GLC_GATEWAY_DB` is required, not tidiness. Every GLC generation on the machine otherwise shares
`~/.glc/gateway.sqlite`, whose `calls` table an older generation created as an append-only ledger
with a `CHECK(schema_version = 2)` column that glc_v5 never writes. glc_v5 creates the table with
`CREATE TABLE IF NOT EXISTS`, so the create silently no-ops and every model call dies at insert time.
The gateway starts healthy and only real traffic fails. Never migrate or delete
`~/.glc/gateway.sqlite`; it is an audit ledger.

## 5. Terminal 2 — product

Start the product **before** S17 the first time. The product seeds and git-initializes
`PTF_WORKSPACE` on first boot, and S17 refuses to open a workspace directory that does not exist yet.
On later restarts the order no longer matters.

```bash
cd "$PTF"
uv sync --locked --dev
uv run physics-toy-factory
```

## 6. Terminal 3 — S17

```bash
cd "$S17"
set -a; source "$S17/.env"; set +a

export GLC_BASE_URL=http://127.0.0.1:8211
export S17_PORT=8213
export S17_DATA_DIR="$RT/s17-data"
export S17_WORKSPACE="$RT/workspace"        # byte-identical to PTF_WORKSPACE
export S17_SANDBOX_ROOT=
export S17_SKILLS_DIR=
export S17_ALLOWED_COMMANDS=node
export S17_PROTECTED_PATHS='.physics-toy-workspace,P5_API.md,p5check.js,shell/**,tests/**,test/**,**/tests/**,**/test_*.py,**/*_test.py,conftest.py,**/conftest.py,pytest.ini,tox.ini,setup.cfg,pyproject.toml,.github/**'
export S17_MAX_REPEAT_FAILURES=3
export S17_EXEC_CONTAINER=1
export S17_EXEC_IMAGE=physics-toy-factory-node:22.20.0-phase6
export DOCKER_HOST=unix://<paste the path from the prerequisite check>

uv run s17code serve
```

Three of these are easy to get wrong:

- **The empty `S17_SANDBOX_ROOT` and `S17_SKILLS_DIR` are deliberate.** `load_dotenv` never overrides
  a variable that is already set, so an empty-but-present value wins while an unset one gets
  repopulated from `$S17/.env`. Leaving them populated hands the planner generic file, indexing,
  calendar, and skill capabilities outside the product workspace, and it will use them.
- **`DOCKER_HOST` is required whenever the daemon is not on `/var/run/docker.sock`**, which is the
  normal case for Docker Desktop on macOS. `s17code/coding/exec.py` gives the checker a scrubbed
  environment and forwards `DOCKER_HOST` only if it is already set. Without it every check exits
  `125` with `Cannot connect to the Docker daemon`.
- **`S17_WORKSPACE` must equal `PTF_WORKSPACE` exactly.** They are one directory shared by two
  processes; a trailing slash difference is fine, a different path is a silent broken run.

Prove the capability profile is what you think it is before serving:

```bash
uv run python -c 'import os,sys; from dotenv import load_dotenv; p=sys.argv[1]; assert os.path.exists(p), p; load_dotenv(p); assert os.getenv("S17_SANDBOX_ROOT") == ""; assert os.getenv("S17_SKILLS_DIR") == ""' "$S17/.env"
```

## 7. Terminal 4 — verify before spending

```bash
curl -sS http://127.0.0.1:8211/healthz          # gateway process
curl -sS http://127.0.0.1:8211/v1/providers     # gateway can actually route
curl -sS http://127.0.0.1:8213/healthz          # S17 process
curl -sS http://127.0.0.1:8213/readyz           # S17 -> gateway reachability
curl -sS http://127.0.0.1:8220/api/health       # the product's own view
```

The gateway serves no `/readyz`; that route belongs to S17, which probes the gateway through it.
`/v1/providers` is what proves the gateway has providers configured.

The product health response must read:

```text
.ready                                 = true
.workspace.verified                    = true
.s17.process.up                        = true
.s17.gateway.ready                     = true
.container_mode.configured             = true
.container_mode.secure_sandbox_claimed = false
```

`secure_sandbox_claimed` is hard-coded `false` and stays `false`. The product reports that container
mode is *configured*; it never claims to have independently verified the runtime is secure.

## 8. Drive it in the browser

Open **http://127.0.0.1:8220/**.

### First: a suggested prompt

1. Click `Rain that avoids my mouse`.
2. Click `Build this toy`.
3. Watch the Live Journal. You should see, in order: planning, `Writing sketch.js`, `Judging the
   simulation`, `Check passed`, `Simulation ready`.
4. The Output Bay should show a `VERIFIED` badge and a `VERIFIED PREVIEW LIVE` marker.
5. Move the mouse over the canvas. It must respond.

### Then: the linked follow-up

1. Click the reset icon (top right) and confirm.
2. Enter `Create a tiny solar system.` and click `Build this toy`.
3. Wait for the verified preview.
4. In `Refine the verified toy`, enter `Make the planets leave glowing trails.`
5. Click `Apply one change`.
6. **The old preview must close while verification runs** — the product never shows an unverified
   revision.
7. The replacement preview must be verified and visibly show trails.
8. The follow-up form must disappear. Exactly one modification is allowed per toy.

### Inspect what actually happened

```bash
curl -sS http://127.0.0.1:8220/api/session | jq '.session | {state, follow_up_used, current_sketch_sha256, runs}'
```

After the linked flow: `state` is `ready`, `follow_up_used` is `true`, two runs are present, the
second run's `parent_run_id` is the first run's `run_id`, both outcomes are `ready`, and the two
verified sketch hashes differ.

To see the money:

```bash
curl -sS "http://127.0.0.1:8211/v1/calls?limit=50" | jq '[.[] | .usd] | add'
```

## 9. Reading the journal honestly

The journal is backed by the real S17 event tape, so it shows failures as readily as successes.

- A `Check failed` followed by an edit and a second `Check passed` is the system working. The planner
  is expected to need repairs.
- Repeated identical failures stop at `S17_MAX_REPEAT_FAILURES` (3). That is a real model failure,
  not a bug to retry away.
- A browser-side runtime error in the sketch is recorded and displayed, but never starts another run.
  Browser-only repair is outside the MVP.

## 10. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Session shows `reset_required` on load | A previous sketch is still in the workspace; a product restart deliberately forgets session links | Click reset and confirm. Expected after any restart. |
| Every check exits `125`, `Cannot connect to the Docker daemon` | `DOCKER_HOST` unset and the daemon is not on `/var/run/docker.sock` | Export it in the S17 terminal from `docker context inspect` |
| Run fails at the very end, after the check passed | Ollama down or `nomic-embed-text` missing — the answer node writes memory through it | Start Ollama, pull the model, rerun |
| Gateway healthy but every run fails with 0 nodes | Shared `~/.glc/gateway.sqlite` schema clash | Set `GLC_GATEWAY_DB` to a private path |
| Product error the moment you press Build | `PTF_S17_CONTROL_TOKEN` ≠ `S17_CONTROL_TOKEN` | Recopy the token |
| Planner reads unexpected files, or uses skills | `S17_SANDBOX_ROOT` / `S17_SKILLS_DIR` were unset rather than empty | Export both as empty, restart S17 |
| `curl :8211/readyz` returns `{"detail":"Not Found"}` | The gateway has no such route | Use `/healthz` and `/v1/providers` |
| S17 refuses to start, workspace not a directory | S17 started before the product ever seeded it | Start the product once, then S17 |
| Preview never appears though the check passed | Preview waits for two animation frames from the trusted shell | Check the browser console; a sketch that never draws will not verify |

## 11. Shut down

Ctrl-C each terminal, or:

```bash
for p in 8220 8213 8211; do
  pid=$(lsof -nP -iTCP:$p -sTCP:LISTEN -t 2>/dev/null | head -1)
  [ -n "$pid" ] && kill "$pid" && echo "stopped :$p"
done
```

State in `$RT` survives and is reused on the next start. To begin completely fresh, delete `$RT` and
restart from §5 — the product will reseed the workspace. S17 journals are durable and reset never
deletes them.
