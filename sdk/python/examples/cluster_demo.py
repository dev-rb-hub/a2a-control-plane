"""End-to-end demo: a two-zone in-process cluster exercising the spec's main behaviors.

Run from sdk/python:  python examples/cluster_demo.py
"""

from __future__ import annotations

from a2a_control_plane import Channel, DevCluster, SpiffeId, WorkerClient
from a2a_control_plane.subjects import agent_subject
from a2a_control_plane.tasks import (
    TASK_STATUS_CANCELLED,
    Task,
    TaskResult,
    make_task,
    succeeded,
)


def step(title: str) -> None:
    print(f"\n== {title}")


def handle(task: Task) -> TaskResult:
    return succeeded(task, b"done:" + task.payload)


def main() -> None:
    results: list[str] = []
    cluster = DevCluster(zones=("eu-west", "us-east"), grace_period=60)
    for aggregator in cluster.aggregators.values():
        aggregator.on_result = lambda agent, result: results.append(
            f"{agent}/{result.task_id} -> "
            + ("CANCELLED" if result.status == TASK_STATUS_CANCELLED else result.payload.decode())
        )
        aggregator.on_task_failed = lambda agent, task: print(f"   task {task} on {agent} FAILED")
        aggregator.on_reassign = lambda task, src, dst: print(
            f"   task {task.task_id} reassigned {src} -> {dst} (attempt {task.attempt})"
        )

    step("Register 3 workers in each zone (mTLS identity + signed challenge)")
    for zone in cluster.aggregators:
        for i in range(3):
            worker = cluster.add_worker(f"{zone}-w{i}", zone, {"model": "gpt", "slots": "4"})
            worker.on_task(handle)
    for zone, aggregator in cluster.aggregators.items():
        states = {a: aggregator.worker_state(a) for a in (f"{zone}-w{i}" for i in range(3))}
        print(f"   {zone}: {', '.join(f'{a}={s}' for a, s in states.items())}")

    step("Dispatch tasks (aggregator -> worker, own zone only)")
    cluster.aggregators["eu-west"].dispatch("eu-west-w0", make_task("t1", b"summarize"))
    cluster.aggregators["us-east"].dispatch("us-east-w1", make_task("t2", b"translate"))
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

    step("Failure handling: eu-west-w1 dies mid-task; its task moves to a healthy worker")
    dead = cluster.workers[1]
    dead.on_task(handle)
    dead.offline = True
    eu = cluster.aggregators["eu-west"]
    eu.dispatch("eu-west-w1", make_task("t3", b"lost"))
    print(f"   after dispatch: {eu.worker_state('eu-west-w1')}")
    cluster.advance(60)  # heartbeats had backed off to 20s, so detection takes 3 x 20s
    print(f"   +60s:  {eu.worker_state('eu-west-w1')}")
    cluster.advance(60)  # grace period (60s) expires
    print(f"   +120s: {eu.worker_state('eu-west-w1')}")
    print("   results:", results[-1])

    step("Cancellation over the control subject")
    slow = next(w for w in cluster.workers if w.identity.agent_id == "us-east-w0")
    slow.on_task(lambda task: None)  # long-running: finishes later via complete()
    slow.on_cancel(lambda task_id: print(f"   worker stopping {task_id}"))
    us = cluster.aggregators["us-east"]
    us.dispatch("us-east-w0", make_task("t4", b"long job"))
    print(f"   running:   {us.worker_state('us-east-w0')}")
    us.cancel("t4", "user aborted")
    print(f"   cancelled: {us.worker_state('us-east-w0')}  ->  {results[-1]}")

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
