# Agent guide

Instructions for AI coding agents working in this repository. Humans should read [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

An open standard for zero-trust, low-noise agent control planes, plus a reference Python SDK. It is independent of the Linux Foundation Agent2Agent (A2A) protocol and bridges to it. Normative text lives in `spec/`; wire schemas in `schemas/v1/`; the SDK in `sdk/python/`.

## Architecture in one screen

- Three roles: **Registry** (admission, authoritative records), **Aggregator** (one per zone; the only path for workers), **Worker**. Workers never talk to each other or to the registry.
- Traffic is subjects on a regional bus (`a2a.zone.<zone>.agent.<agent>.{tasks,state,results,control}` and `a2a.zone.<zone>.aggregator.state`). Authorization is derived only from the SPIFFE ID of the session (`subjects.may_publish` / `may_subscribe`).
- State is sent as deltas and adaptive heartbeats; the aggregator coalesces and reports to the registry.
- SDK layers: core (pure logic) -> roles (`registry`, `aggregator`, `worker`) over `bus.InMemoryBus` -> `dev` (simulation) -> `bridge` (optional `a2a-sdk` integration).

## Rules

1. Spec, schema, code and tests move together. If you change behavior, change the matching `spec/` section and add or update tests in the same change.
2. Schemas are append-only in `v1`. Never renumber, retype or remove a field. After editing a `.proto`, run `python scripts/gen_proto.py` from `sdk/python`. Never edit `_proto/` by hand. The pinned `grpcio-tools` version is deliberate (it sets the minimum protobuf runtime).
3. Use RFC 2119 words in capitals only for normative requirements.
4. Core modules stay free of I/O and take time as an argument or a clock. Tests drive `DevCluster` with `ManualClock`.
5. Do not weaken security checks (ACLs, identity binding, challenge single-use) to make a test pass. Add a test for the denial instead.
6. Do not claim features in READMEs that are not implemented. Update the status table in `sdk/python/README.md`.

## Commands (from `sdk/python`)

```
pytest -q
ruff check . && ruff format --check .
mypy src examples
python scripts/gen_proto.py && git diff --exit-code -- src/a2a_control_plane/_proto
python examples/cluster_demo.py
```

All of these must pass before you finish. They also run in CI.

## Where the work is

Open GitHub issues are the backlog, labeled by area (`spec`, `sdk`, `transport`, `bridge`). Issues marked `agent-friendly` are self-contained: they name the files to change and the acceptance tests. Prefer those. Large design questions are labeled `design` and need a discussion on the issue before code.

## Known sharp edges

- The roles are synchronous and driven by an explicit clock. Real transports are async; an issue tracks how to reconcile this. Do not add hidden threads or global event loops to the roles.
- Registration challenges are still direct method calls (no RPC schema yet).
- `bridge.py` runs each task in its own event loop when none is running; inside a running loop it defers completion and supports cancellation.
