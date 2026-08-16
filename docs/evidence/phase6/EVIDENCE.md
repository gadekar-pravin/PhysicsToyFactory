# Phase 6 qualification evidence

Recorded on 2026-08-15 and 2026-08-16 UTC. Live qualification is a release/demo gate, separate from
deterministic CI. This record preserves the original failed qualification, the remediation canary,
and the later successful full requalification as distinct evidence sets.

Each section states the revisions its runs were produced at, and those statements are never revised.
The dependency revisions have since been superseded; read the closing addendum before reproducing
anything here.

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

## Historical release decision and unresolved limitations

At the time of the 2026-08-15 evidence set, the Phase 6 implementation and deterministic gates
passed, but the live Phase 6 release gate did not. There was no verified linked preview, so
browser/manual screenshot capture was correctly not run and the specified MVP was not yet
live-qualified.

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

## Full post-remediation requalification: passed

The explicitly authorized full suite ran once on 2026-08-16 with no retries. It used a fresh S17
data directory, fresh gateway ledger, and fresh product workspace. Every run had a `$0.50` ceiling,
reached a verified `ready` outcome, executed a passing network-disabled container checker, and
finished through a succeeded `answer_with_evidence` node.

- PhysicsToyFactory runtime revision: `2b8a834aeb75446e73d0404df8520fd4ff042b0d`
- S17Code revision: `4e085cb2e869694f036df2a6171530e147077e85`
- glc_v5 revision: `66ed155addd78fe8f59673ddca59e0277a7d39e8`
- Judge image ID: `sha256:1592c97f88b3d45ad796dd292877010dc5bbf2d6b90a7742f4507ed3ae6d4524`
- Retry count: zero

| Scenario | Run ID | Latest checker | S17 controller spend |
| --- | --- | --- | ---: |
| Rain that avoids my mouse | `run-13fd7410c11d` | exit 0, 1,845 draw calls | `$0.01457015` |
| Bouncy magnets | `run-f0bf3f8a7f50` | exit 0, 130 draw calls | `$0.06352438` |
| Angry solar system | `run-221f5bd6bfaf` | exit 0, 835 draw calls | `$0.03196028` |
| Fish that follow my cursor | `run-132fb4549496` | exit 0, 515 draw calls | `$0.04699585` |
| Create a tiny solar system. | `run-5578e66df13b` | exit 0, 960 draw calls | `$0.02824415` |
| Make the planets leave glowing trails. | `run-ddb203fdabda` | exit 0, 1,120 draw calls | `$0.07379521` |

The follow-up is product-linked to `run-5578e66df13b` and replaced its verified sketch revision with
SHA-256 `63af056215249c0aa0d32068c0be5227371e8d8e42c5be1e672c16e48bcc9c0f`.
All 59 model calls reported concrete routes: 53 planner calls used
`openrouter/google/gemini-3.7-flash`, three answer calls used
`gemini_1/gemini-3.5-flash-lite`, and three used `gemini_2/gemini-3.5-flash-lite`.

S17 controller metering totaled `$0.25909001`; the fresh gateway list-price ledger totaled
`$0.37562363` for the same 59 calls. These are reported separately because they are different
accounting views. The final browser observation passed with a visible, pointer-responsive canvas in
the sandboxed verified iframe and no system-error banner.

The selected six graphs, six event tapes, summary, and screenshot are in
`full-requalification-2026-08-16/`. Their recorded hashes were rechecked after browser capture, and
the selected evidence contains no authorization material or machine-local paths.

## Final release decision

The full Phase 6 live gate now passes. Together with the green deterministic gates, this completes
the specified MVP qualification. Browser-only automatic repair, Surprise Me, skill A/B,
planted-refusal demonstrations, persistent multi-user sessions, and additional follow-ups remain
outside the MVP boundary.

## Addendum, 2026-08-16 UTC: dependency revisions superseded

This addendum records a later change to the dependency revisions. It amends nothing above. Every
run, run ID, hash, route, and spend figure recorded earlier was produced at the revisions stated in
those sections and remains the record of live behaviour at those revisions.

Two statements above are stale as descriptions of the present, though both were accurate when
written:

- "It is pushed on `phase6-container-command-result`; it is not part of S17 main." That branch has
  since been merged. It was rebased before merge, so the qualifying commit
  `4e085cb2e869694f036df2a6171530e147077e85` is not itself on `main`; its content is, as `d0c2720`,
  merged by `7bf4b1e937699449a6a883e4862184559db1a91b`.
- The S17 live dependency revision is no longer reachable. `4e085cb` is contained by no ref, local or
  remote, and will be discarded whenever `git gc` prunes unreachable objects. It cannot be assumed
  available for future reproduction.

`docs/PHASE6_RUNBOOK.md` was therefore re-pinned in `2b1eb7b`:

| Repository | Runbook pin from 2026-08-16 | Qualified at, above |
| --- | --- | --- |
| S17Code | `7bf4b1e937699449a6a883e4862184559db1a91b` | `4e085cb2e869694f036df2a6171530e147077e85` |
| glc_v5 | `77054f4b7a4d9879d33c5221ff08a35fdf48eb10` | `66ed155addd78fe8f59673ddca59e0277a7d39e8` |

### Reviewed deltas between the qualified and re-pinned revisions

- glc_v5 `66ed155..77054f4`: `.env.example` only. Documentation; no runtime behaviour.
- S17Code `4e085cb..7bf4b1e`: a README authorization example, added tests, and `num_ctx=512` pinned on
  `nomic-embed-text` requests in `s17code/core/memory/embeddings.py`.

The embedding change is on the qualified live path, not beside it: `runtime.py` constructs the
embedder for every run, and a succeeded `answer_with_evidence` writes an episode through
`MemoryStore.write`. Embedding text representative of a recorded answer, with and without the pin, on
the same local `nomic-embed-text` runner produced byte-identical 768-dimension vectors with a maximum
absolute drift of `0.000e+00`. The pin moves Ollama's truncation point to one the runner survives; it
does not alter vectors for text that already fits.

### Deterministic verification at the re-pinned revisions

```text
S17Code    uv run ruff check .                 PASS
S17Code    uv run pytest -q                    PASS: 498 passed, 1 skipped
S17Code    Phase 0 offline budget proof        PASS
S17Code    Phase 0 offline denial-of-wallet    PASS
S17Code    Phase 0 offline trace-export proof  PASS
Product    uv run ruff check .                 PASS
Product    uv run pytest -q -m "not browser"   PASS: 183 passed, 8 deselected
Product    uv run pytest -q -m browser         PASS: 8 passed, 183 deselected
```

The container checker path was exercised directly at the re-pinned S17 revision: `run_command`
returned exit `0` from the pinned image under `--network=none`, with a JSON-serialisable result. The
judge image is unchanged at
`sha256:1592c97f88b3d45ad796dd292877010dc5bbf2d6b90a7742f4507ed3ae6d4524`.

The same probe with `DOCKER_HOST` unset returned exit `125` and
`Cannot connect to the Docker daemon at unix:///var/run/docker.sock`, reproducing the defect that
stopped `run-e48a22aa0e57`. That variable is now a required export in the runbook.

### Unresolved limitation

**No live model run has been executed at the re-pinned revisions.** The deterministic gates above are
not a live gate, and this addendum does not extend the passing live qualification to them. Qualifying
at the re-pinned revisions requires a separately authorized canary followed by the full suite,
published as its own evidence set. The retained live pass remains attributable only to the revisions
recorded in the sections above.
