# RFC 0005 (proposed): Concurrency model for the SDK roles and transports

Issue [#5](https://github.com/dev-rb-hub/a2a-control-plane/issues/5). Blocks #6 (gRPC), #7 (NATS). Wire impact: none. Spec impact: one informative note. Status: proposed; discussion is on #5.

The benchmark harness used for the measurements in section 2 is not in the repository yet (see the work plan, step 5).

## 1. Facts from the code
- Roles (`aggregator.py`, `registry.py`, `worker.py`) are synchronous, take time as an argument, hold unsynchronised dict state, and are driven by synchronous bus callbacks.
- `InMemoryBus` delivers synchronously, so a publish can re-enter a role on the same stack. `worker.py` already commits state before publishing for this reason (a ResyncRequest can arrive mid-publish).
- `TaskHandler` may return None and finish later via `WorkerClient.complete`: deferred completion already exists.
- `bridge.py` already behaves differently inside and outside a running loop; a2a-sdk handlers are async; the CrewAI scaffold methods are `async def`.
- Tests rely on `ManualClock` and synchronous delivery (313 tests at the time of PR #20).

## 2. Evidence
Workload shape (traffic_benchmark.py, extrapolated): 1,000 agents ~ 79,000 msg/h ~ 22 msg/s; 10,000 agents ~ 220 msg/s steady state. Idle connections dominate, not throughput.

Measured: grpcio 1.85, bidi streaming, 32-byte echo handler, loopback, Windows 11, 16 logical cores, Python 3.14, 4 client processes. Trivial handler, not a production profile.

| Streams | Mode | Server RSS | Per idle stream | Threads | Echo msg/s (100 active) | p50 / p99 ms |
|---|---|---|---|---|---|---|
| 100 | sync | 41.2 MB | 90 KB | 123 | 7,314 | 13.5 / 18.8 |
| 100 | aio | 36.0 MB | 37 KB | 24 | 4,272 | 22.9 / 32.5 |
| 1,000 | sync | 111.2 MB | 81 KB | 1,023 | 6,959 | 14.1 / 19.8 |
| 1,000 | aio | 59.2 MB | 27 KB | 24 | 4,116 | 23.9 / 32.7 |

Baseline server RSS 32.4 MB. Idle CPU 0%. sync is ~1.7x faster on the echo test; cause NOT root-caused (hypothesis: per-message coroutine and thread-handoff overhead in the aio path; unverified). The sync server is configured with one pool thread per stream, so its thread count scales with streams by construction.

### Benchmark Results (second session: 2,000-stream attempt)

Same harness, machine and versions as above. Each case had a 5-minute limit; every failure below happened within 1-2 s of start.

| Case | Client procs | Result |
|---|---|---|
| sync 1,000 (control) | 4 | OK: 80.5 KB/stream, 1,023 threads, 8,137 msg/s, p50 12.05 / p99 17.59 ms (matches the earlier run) |
| sync 2,000 | 4 | FAILED, reproduced twice |
| sync 2,000 | 8 | FAILED |
| aio 2,000 | 4 | FAILED |
| aio 1,500 | 4 | FAILED |
| aio 1,200 | 4 | FAILED |

Failure mode: every client process died while opening streams with `AioRpcError: StatusCode.INTERNAL, "Internal error from Core"` (`Failed "execute_batch"` on a SendMessage operation), so the harness asserted before taking any measurement. No server-side message was captured.

What this does and does not show: the failure happens in both sync (thread per stream) and aio (24 threads), and the 1,000-stream control still passes, so the harness works up to 1,000 streams on this Windows host and fails somewhere between 1,000 and 1,200. It is NOT evidence about sync vs aio scaling and NOT evidence that either model cannot reach 2,000 streams. The cause is NOT root-caused; candidates (unverified) are a gRPC core or Windows limit on this host, or a harness defect (for example many channels per client process). No 2,000+ stream numbers exist. Next steps: capture server stderr and the client's first error, bisect between 1,000 and 1,200, and repeat on Linux.

Planned, NOT measured (mark "planned" in any table): 2,000 / 5,000 / 10,000 streams for sync and aio (blocked by the failure above; the first 5,000-stream sync attempt in an earlier session also produced no result); the same on Linux; reconnect storm; role logic on one loop with real protobuf messages; container PID limits vs thread-per-stream. Extrapolation to replace with measurement: 10,000 streams ~0.8 GB / 10,000 threads (sync), ~0.27 GB / ~24 threads (aio).

## 3. Option A: asyncio-native core
Roles become async; Bus/Session async; tick() becomes loop timers. bus.py: publish and handlers become coroutines and synchronous delivery goes away, so reentrancy assumptions must be re-audited. worker.py: register/update_capabilities/report_fault awaitable; TaskHandler awaitable. Streaming: natural fit for grpc.aio and nats-py; backpressure via bounded asyncio.Queue. Lowest memory and thread count; one core per process, scaled horizontally by Aggregator instances (spec 05 4.2). Hurdles: sync LLM code blocks the loop; function colour spreads through roles and 313 tests; DevCluster determinism needs a new scheduler. Threaded agent bridge: run sync handlers in a bounded ThreadPoolExecutor via run_in_executor, return results with loop.call_soon_threadsafe, never touch role state from the worker thread; thread cancellation is cooperative only (an Event), async tasks use Task.cancel().

## 4. Option B: threaded sync gRPC core
Roles stay sync; grpc.server(ThreadPoolExecutor). Each open streaming RPC holds a pool thread for its lifetime; max_workers must be >= stream count or streams queue and starve (measured: 1,023 threads for 1,000 streams). ~80-90 KB RSS/stream plus a reserved stack per thread (~1 MB virtual on Windows, typically 8 MB virtual on Linux). OS/container thread and PID limits cap streams below 10,000 (general knowledge, unverified here). Locking: shared role state needs locks and reentrant delivery means a lock held across publish can deadlock; one coarse RLock per role serialises everything. Simplest to use and highest throughput at low stream counts; sync agent code just works.

## 5. Matrix
| Criterion | A: asyncio-native | B: threaded sync |
|---|---|---|
| Throughput, few streams (measured echo) | lower (~4.1-4.3k msg/s) | higher (~7.0-7.3k msg/s) |
| Throughput at 10k streams | planned | planned; capped by threads and locks |
| Memory predictability | best: ~27-37 KB/stream, flat threads | worse: ~80-90 KB/stream plus a stack per thread; OS caps |
| Implementation complexity | high: rewrite roles, async-colour tests, new scheduler | low transport; moderate locking and reentrancy |
| Test determinism | needs new machinery | unchanged |
| a2a-sdk (checked) | native | needs a loop in a thread |
| CrewAI / LangGraph | both offer sync and async entry points (general knowledge, NOT checked against current versions); sync handlers need an executor | sync handlers run directly |
| Reconnect storm | planned | planned |

## 6. Recommendation
Keep the core synchronous and sans-I/O; drive it from one event-loop owner with grpc.aio at the edge (option 1 of #5).
1. Core unchanged: roles stay sync, time-as-argument, no I/O. ManualClock and the synchronous InMemoryBus stay for tests. Enforce with an import ban on asyncio and threading in core modules.
2. Single owner: a runtime owns one event loop (in a dedicated thread for sync callers). Only that thread touches role state, so no locks. Transports call into roles on the loop. tick() runs from a loop timer.
3. Outbound is enqueue-only: Session.publish puts onto a bounded queue instead of calling handlers synchronously.
4. User code off the loop: sync handlers in a bounded executor, async handlers awaited, results return via call_soon_threadsafe into WorkerClient.complete.
5. Sync facade for users who want no async.
6. grpcio and nats-py are optional extras; core install stays protobuf + cryptography.

Why not A: rewrites tested logic and loses deterministic tests for a benefit the transport edge already captures. Why not B: the thread-per-stream cap and locking over reentrant roles are the wrong ceiling for thousands of idle workers.

Protocol compliance: no wire change. One informative note in spec 05: per-session messages are processed in order (the sequence rules already assume it) and a Worker's state transitions are atomic; everything else implementation-defined.

Risks: single-loop CPU ceiling (scale by instances; re-measure with real protobuf traffic); accidental loop blocking (CI slow-callback test plus lint); gap between synchronous in-memory delivery and queued delivery (conformance suite #3 runs roles against both; audit reentrancy points); the unexplained sync-vs-aio throughput gap (put on the benchmark list before the decision is final).

## 7. Work plan
1. Decision on #5 (14-day discussion window if treated as a spec change, per GOVERNANCE.md).
2. Bus/Session protocols and conformance suite (#3).
3. Runtime: loop owner, executor offload, sync facade, tests on a real loop.
4. grpc.aio transport (#6), then NATS (#7).
5. Benchmarks from section 2 as `sdk/python/benchmarks/`, run on Linux.
