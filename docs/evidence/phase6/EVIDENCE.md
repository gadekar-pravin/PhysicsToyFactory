# Phase 6 qualification evidence

Recorded on 2026-08-15 UTC. Live qualification is a release/demo gate, separate from deterministic
CI. This record distinguishes green implementation checks from the unresolved live outcome.

## Source and runtime profile

- PhysicsToyFactory base revision: `e43989e111be2a16dfad27591afd606a7e6d9db4`
- S17Code main revision: `b001953ed7f898eb98d9054cc5155f2038655d98`
- S17Code live dependency revision: `4e085cb2e869694f036df2a6171530e147077e85`
- glc_v5 revision: `66ed155addd78fe8f59673ddca59e0277a7d39e8`
- S17 endpoint: local `127.0.0.1:8113`; product endpoint: local `127.0.0.1:8120`
- Judge image: `physics-toy-factory-node:22.20.0-phase6`
- Built image ID: `sha256:1592c97f88b3d45ad796dd292877010dc5bbf2d6b90a7742f4507ed3ae6d4524`
- Base image: `node:22.20.0-alpine@sha256:dbcedd8aeab47fbc0f4dd4bffa55b7c3c729a707875968d467aaaea42d6225af`
- Container user: `node`, UID `1000`; generated-code network mode: `none`

The live S17 dependency revision adds JSON-safe `run_command` results and explicit local Unix Docker
socket propagation. It is pushed on `phase6-container-command-result`; it is not part of S17 main.

## Deterministic gates

PhysicsToyFactory:

```text
uv sync --locked --dev                         PASS
uv run ruff check .                            PASS
uv run pytest -q -m "not browser"              PASS: 183 passed, 8 deselected
uv run pytest -q -m browser                    PASS: 8 passed, 183 deselected
```

S17Code at the live dependency revision:

```text
uv run ruff check .                            PASS
uv run pytest -q                               PASS: 493 passed, 1 skipped
Phase 0 offline budget proof                   PASS
Phase 0 offline denial-of-wallet proof         PASS
Phase 0 offline trace-export proof             PASS, including node spans
```

The real container checker smoke test also passed with exit `0`, five frames, and ten draw calls.

## Genuine repair proof: passed

- Run ID: `run-2d376894a75f`
- Recorded at: `2026-08-15T17:17:44Z`
- Red checker: `check_before_edit`, sequence 5, exit 1
- Anchored edit: `edit_sketch`, sequence 10, exactly one occurrence replaced
- Latest green checker: `check_after_edit`, sequence 13, exit 0
- Final sketch SHA-256: `e3beb62d2f0e4b4f3938b4515e2cdacf27217f4c65934c0781fb13193826d441`
- Reported routes: `openrouter/google/gemini-3.7-flash` and
  `gemini_2/gemini-3.5-flash-lite`

The selected raw graph, actual event tape, and fail-closed proof summary are in `repair-proof/`.

Two earlier authorized attempts exposed infrastructure defects and were stopped without being
misreported as agent failures:

- `run-f3f61670086a`: command evidence was not JSON serializable.
- `run-e48a22aa0e57`: the child process lost the Docker Desktop Unix socket and attempted the default
  `/var/run/docker.sock`.

Both defects are covered by regression tests in the S17 dependency revision above.

## Product qualification: failed

All four advertised prompts and the exact solar create were attempted once. Each real S17 graph
finished without `answer_with_evidence`, so the product classified it as `answer_missing`:

| Scenario | Run ID | Outcome |
| --- | --- | --- |
| Rain that avoids my mouse | `run-95d6905c1c06` | failed: `answer_missing` |
| Bouncy magnets | `run-97eb34a7c9a6` | failed: `answer_missing` |
| Angry solar system | `run-d9affcba0b99` | failed: `answer_missing` |
| Fish that follow my cursor | `run-91b5c1490066` | failed: `answer_missing` |
| Create a tiny solar system. | `run-94e05d882fca` | failed: `answer_missing` |
| Make the planets leave glowing trails. | no run | not started: product returned HTTP 409 |

The failed creation graphs do not report a complete provider/model pair, so their route arrays remain
empty rather than inferring a route. Sanitized graphs, event tapes, hashes, timestamps, and the exact
observed outcome are retained in `live-qualification/`.

An earlier preflight run, `run-414aa89445e4`, was excluded from the qualifying set after the evidence
collector used a relative SSE URL. It also revealed generic S17 sandbox/skill roots in the launch
environment. The runbook now explicitly clears those roots before applying the narrow product
profile.

## Release decision and unresolved limitations

The Phase 6 implementation and deterministic gates pass. The live Phase 6 release gate does not.
There is no verified linked preview, so browser/manual screenshot capture was correctly not run and
the specified MVP must not yet be described as live-qualified.

At the time of this failed evidence set, the required next step was to diagnose why the live S17
graphs repeatedly finished without `answer_with_evidence`. The separately approved post-remediation
canary is recorded below.

## Post-remediation solar canary: passed

One separately authorized canary was run on 2026-08-16 after attaching a `$0.50` product-run budget
and correcting the dotenv-resistant S17 capability profile. No retry was used.

- PhysicsToyFactory revision: `90fdeafb3e94dd71967e2fbc06d0d9b17a795a81`
- S17Code revision: `4e085cb2e869694f036df2a6171530e147077e85`
- glc_v5 revision: `66ed155addd78fe8f59673ddca59e0277a7d39e8`
- Prompt: `Create a tiny solar system.`
- Run ID: `run-1eb7223615ef`
- Recorded at: `2026-08-16T08:39:56Z`
- Outcome: ready, with a succeeded `answer_with_evidence` node
- Latest and only checker: exit `0`, `P5CHECK PASS frames=5 draw_calls=805`
- Checker cage: pinned non-root image with `--network=none`
- Sketch SHA-256: `a45ceeaacef07afb8ae7d0286ba2e52d992fcc5609b51955cf2df82d3351fc77`
- Model routes: `openrouter/google/gemini-3.7-flash` for seven planner calls and
  `gemini_1/gemini-3.5-flash-lite` for one answer call
- S17 controller spend: `$0.02610480` of the `$0.50` ceiling
- Gateway list-price ledger: `$0.03809030` across the same eight calls
- Browser observation: passed; verified sandboxed preview and interactive canvas visible with no
  system-error banner

The selected graph, event tape, summary, and screenshot are in `canary-2026-08-16/`. This proves the
minimal remediation on the demo-critical creation path. It does not complete the full Phase 6 gate:
the four suggested prompts and linked glowing-trails follow-up have not been rerun.
