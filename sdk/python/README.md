# a2a-control-plane (Python SDK)

Reference Python SDK for the [a2a-control-plane specification](../../spec/). Pre-alpha.

This package is independent of, and not affiliated with, the Linux Foundation Agent2Agent protocol or its `a2a-sdk`.

## Layers

| Layer | Modules | Status |
|-------|---------|--------|
| Core (pure logic, no I/O) | `tokens`, `identity`, `subjects`, `state`, `heartbeat`, `delta` | implemented |
| Roles over a transport interface | `WorkerClient`, `Aggregator`, `Registry` | planned |
| Transports | in-memory, gRPC + mTLS, NATS | planned |
| Bridge to the A2A protocol `a2a-sdk` | task dispatch via `…agent.<id>.tasks` | planned |

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts/gen_proto.py   # regenerate protobuf bindings from ../../schemas/v1
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy src
```
