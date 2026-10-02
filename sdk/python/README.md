# a2a-control-plane (Python SDK)

Reference Python SDK for the [a2a-control-plane specification](../../spec/). Pre-alpha.

This package is independent of, and not affiliated with, the Linux Foundation Agent2Agent protocol or its `a2a-sdk`.

## Layers

| Layer | Modules | Status |
|-------|---------|--------|
| Core (pure logic, no I/O) | `tokens`, `identity`, `subjects`, `state`, `heartbeat`, `delta`, `tasks`, `control`, `reports` | implemented |
| Roles over an in-memory bus | `Registry`, `Aggregator`, `WorkerClient`, `InMemoryBus` | implemented |
| Dev tooling | `DevCA` (Ed25519, not SPIRE), `ManualClock`, `DevCluster` | implemented |
| A2A bridge, worker side | `bridge` (run an `a2a-sdk` agent as a worker; extra `a2a`) | implemented |
| Registration RPC schema | [#2](https://github.com/dev-rb-hub/a2a-control-plane/issues/2) | planned |
| Bus protocol, X.509 dev certificates, async design | [#3](https://github.com/dev-rb-hub/a2a-control-plane/issues/3), [#4](https://github.com/dev-rb-hub/a2a-control-plane/issues/4), [#5](https://github.com/dev-rb-hub/a2a-control-plane/issues/5) | planned |
| Transports | gRPC + mTLS ([#6](https://github.com/dev-rb-hub/a2a-control-plane/issues/6)), NATS ([#7](https://github.com/dev-rb-hub/a2a-control-plane/issues/7)) | planned |
| A2A gateway, multi-turn tasks | [#8](https://github.com/dev-rb-hub/a2a-control-plane/issues/8), [#9](https://github.com/dev-rb-hub/a2a-control-plane/issues/9) | planned |
| CrewAI integration | `integrations/crewai` (extra `crewai`) | scaffold only, methods raise `NotImplementedError` |

See [CONTRIBUTING.md](../../CONTRIBUTING.md) and [AGENTS.md](../../AGENTS.md) to pick something up.

## Try it

```powershell
.venv\Scripts\python examples\cluster_demo.py        # two-zone cluster: register, dispatch, isolate, fail, recover
.venv\Scripts\python examples\traffic_benchmark.py   # mesh vs hub vs delta + adaptive heartbeats
```

Tasks, results, control commands and zone reports use the messages in `schemas/v1` (`task.proto`, `control.proto`, `zone_report.proto`; spec 03, sections 7-9). The aggregator reassigns retryable failures and tasks of lost workers, supports cancellation and heartbeat configuration, and reports zone state to the registry over the bus. Registration challenges are still direct calls because no RPC schema exists yet ([#2](https://github.com/dev-rb-hub/a2a-control-plane/issues/2)).

## A2A bridge

```python
from a2a_control_plane.bridge import A2AWorkerBridge

bridge = A2AWorkerBridge(my_agent_executor, my_agent_card)   # any a2a-sdk AgentExecutor
worker = cluster.add_worker("agent-1", capabilities=bridge.capabilities())
bridge.attach(worker)
```

Control-plane tasks become A2A messages (text, raw bytes or a serialized `Message`), Agent Card skills become capabilities, and control-plane cancellation calls the agent's `cancel`. A2A `INPUT_REQUIRED` and `AUTH_REQUIRED` are reported as failures because control-plane tasks are single-turn ([#9](https://github.com/dev-rb-hub/a2a-control-plane/issues/9)).

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts/gen_proto.py   # regenerate protobuf bindings from ../../schemas/v1
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy src examples
```
