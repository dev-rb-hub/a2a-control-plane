"""End-to-end demo: a two-zone in-process cluster exercising the spec's main behaviors.

Run from sdk/python:  python examples/cluster_demo.py
"""

from __future__ import annotations

from a2a_control_plane import Channel, DevCluster, SpiffeId, WorkerClient
from a2a_control_plane.subjects import agent_subject


def step(title: str) -> None:
    print(f"\n== {title}")


def main() -> None:
    results: list[str] = []
    cluster = DevCluster(zones=("eu-west", "us-east"), grace_period=60)
    for aggregator in cluster.aggregators.values():
        aggregator._on_result = lambda agent, task, payload: results.append(
            f"{agent}/{task} -> {payload.decode()}"
        )
        aggregator._on_task_failed = lambda agent, task: print(f"   task {task} on {agent} FAILED")

    step("Register 3 workers in each zone (mTLS identity + signed challenge)")
    for zone in cluster.aggregators:
        for i in range(3):
            worker = cluster.add_worker(f"{zone}-w{i}", zone, {"model": "gpt", "slots": "4"})
            worker.on_task(lambda task_id, payload: b"done:" + payload)
    for zone, aggregator in cluster.aggregators.items():
        states = {a: aggregator.worker_state(a) for a in (f"{zone}-w{i}" for i in range(3))}
        print(f"   {zone}: {', '.join(f'{a}={s}' for a, s in states.items())}")

    step("Dispatch tasks (aggregator -> worker, own zone only)")
    cluster.aggregators["eu-west"].dispatch("eu-west-w0", "t1", b"summarize")
    cluster.aggregators["us-east"].dispatch("us-east-w1", "t2", b"translate")
    print("   results:", results)

    step("Zero-trust: lateral movement and cross-zone join are blocked")
    attacker = cluster.workers[0]
    assert attacker.session is not None
    for target in ("eu-west-w1",):
        try:
            attacker.session.publish(agent_subject("eu-west", target, Channel.TASKS), b"evil")
        except PermissionError as error:
            print(f"   worker->worker publish denied: {error}")
    intruder = WorkerClient(
        cluster.ca.issue(SpiffeId.worker(cluster.trust_domain, "us-east", "intruder")),
        cluster.aggregators["eu-west"],
        cluster.clock,
    )
    try:
        intruder.register({"x": "1"})
    except PermissionError as error:
        print(f"   cross-zone registration denied: {error}")

    step("Failure handling: eu-west-w2 goes silent")
    victim = cluster.workers[2]
    victim.offline = True
    cluster.advance(16)
    print(f"   after 16s: {cluster.aggregators['eu-west'].worker_state('eu-west-w2')}")
    victim.offline = False
    cluster.advance(1)
    print(f"   recovered: {cluster.aggregators['eu-west'].worker_state('eu-west-w2')}")

    step("Failure handling: eu-west-w1 dies mid-task and never returns")
    dead = cluster.workers[1]
    dead.on_task(lambda *_: b"")
    dead.offline = True
    eu = cluster.aggregators["eu-west"]
    eu.dispatch("eu-west-w1", "t3", b"lost")
    print(f"   after dispatch: {eu.worker_state('eu-west-w1')}")
    cluster.advance(60)  # heartbeats had backed off to 20s, so detection takes 3 x 20s
    print(f"   +60s:  {eu.worker_state('eu-west-w1')}")
    cluster.advance(60)  # grace period (60s) expires
    print(f"   +120s: {eu.worker_state('eu-west-w1')}")

    step("Registry view")
    for record in cluster.registry.workers():
        print(f"   {record.identity}  {record.state}  {record.view.capabilities}")

    stats = cluster.bus.stats
    print(
        f"\nbus: {stats.published} published, {stats.delivered} delivered, "
        f"{stats.bytes_published} bytes, {stats.denied} denied"
    )


if __name__ == "__main__":
    main()
