# a2a-control-plane (Python SDK)

Reference Python SDK for the [a2a-control-plane specification](../../spec/). Pre-alpha.

This package is independent of, and not affiliated with, the Linux Foundation Agent2Agent protocol or its `a2a-sdk`.

## Layers

| Layer | Modules | Status |
|-------|---------|--------|
| Core (pure logic, no I/O) | `tokens`, `identity`, `subjects`, `state`, `heartbeat`, `delta` | implemented |
| Roles over an in-memory bus | `Registry`, `Aggregator`, `WorkerClient`, `InMemoryBus` | implemented |
| Dev tooling | `DevCA` (Ed25519, not SPIRE), `ManualClock`, `DevCluster` | implemented |
| Transports | gRPC + mTLS, NATS | planned |
| Bridge to the A2A protocol `a2a-sdk` | task dispatch via `…agent.<id>.tasks` | planned |

## Try it

```powershell
.venv\Scripts\python examples\cluster_demo.py        # two-zone cluster: register, dispatch, isolate, fail, recover
.venv\Scripts\python examples\traffic_benchmark.py   # mesh vs hub vs delta + adaptive heartbeats
```

Tasks, results, control commands and zone reports use the messages in `schemas/v1` (`task.proto`, `control.proto`, `zone_report.proto`; spec 03, sections 7-9). The aggregator reassigns retryable failures and tasks of lost workers, supports cancellation and heartbeat configuration, and reports zone state to the registry over the bus. Registration challenges are still direct calls because no RPC schema exists yet.

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts/gen_proto.py   # regenerate protobuf bindings from ../../schemas/v1
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy src
```
