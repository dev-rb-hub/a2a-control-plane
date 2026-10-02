from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("a2a")

from a2a.helpers.proto_helpers import (  # noqa: E402
    get_message_text,
    new_task_from_user_message,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.tasks import TaskUpdater  # noqa: E402
from a2a.types import a2a_pb2 as pb  # noqa: E402

from a2a_control_plane import DevCluster, WorkerState, make_task  # noqa: E402
from a2a_control_plane.bridge import (  # noqa: E402
    A2A_MESSAGE_TYPE,
    A2AWorkerBridge,
    capabilities_from_card,
)
from a2a_control_plane.tasks import (  # noqa: E402
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCEEDED,
    TaskResult,
)

CARD = pb.AgentCard(
    name="test-agent",
    version="1.2",
    skills=[pb.AgentSkill(id="shout", name="Shout"), pb.AgentSkill(id="quiet")],
)


class Agent(AgentExecutor):
    """Behaviour is selected by the text of the incoming message."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        mode = get_message_text(context.message)
        if mode.startswith("say "):
            await event_queue.enqueue_event(new_text_message(mode[4:].upper()))
            return
        if mode == "raise":
            raise RuntimeError("boom")
        task = new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if mode == "fail":
            await updater.failed()
        elif mode == "input":
            await updater.requires_input()
        elif mode == "reject":
            await updater.reject()
        elif mode == "slow":
            await asyncio.sleep(30)
            await updater.complete()
        else:
            await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.cancelled.append(context.task_id or "")
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def setup(agent: Agent) -> tuple[DevCluster, A2AWorkerBridge, list[TaskResult]]:
    cluster = DevCluster()
    results: list[TaskResult] = []
    cluster.aggregators["z1"].on_result = lambda _agent, result: results.append(result)
    bridge = A2AWorkerBridge(agent, CARD)
    worker = cluster.add_worker("a2a-1", capabilities=bridge.capabilities())
    bridge.attach(worker)
    return cluster, bridge, results


def text_task(task_id: str, text: str):  # type: ignore[no-untyped-def]
    return make_task(task_id, text.encode(), content_type="text/plain")


# --- capabilities ------------------------------------------------------------------------


def test_card_becomes_capabilities() -> None:
    assert capabilities_from_card(CARD) == {
        "a2a.agent": "test-agent",
        "a2a.version": "1.2",
        "a2a.skill.shout": "Shout",
        "a2a.skill.quiet": "quiet",  # falls back to the id when the skill has no name
    }


def test_capabilities_reach_the_registry() -> None:
    cluster, _bridge, _ = setup(Agent())
    cluster.advance(2)
    [record] = cluster.registry.workers()
    assert record.view.capabilities["a2a.skill.shout"] == "Shout"


# --- synchronous mode --------------------------------------------------------------------


def test_message_reply_is_returned_as_serialized_a2a_message() -> None:
    cluster, _, results = setup(Agent())
    assert cluster.aggregators["z1"].dispatch("a2a-1", text_task("t1", "say hello"))
    [result] = results
    assert result.status == TASK_STATUS_SUCCEEDED
    assert result.content_type == A2A_MESSAGE_TYPE
    assert get_message_text(pb.Message.FromString(result.payload)) == "HELLO"


def test_task_flow_completes_and_runs_repeatedly() -> None:
    cluster, _, results = setup(Agent())
    for i in range(3):  # several runs, each in its own event loop
        cluster.aggregators["z1"].dispatch("a2a-1", text_task(f"t{i}", "work"))
    assert [r.status for r in results] == [TASK_STATUS_SUCCEEDED] * 3
    assert pb.Task.FromString(results[0].payload).status.state == pb.TaskState.TASK_STATE_COMPLETED
    assert cluster.aggregators["z1"].worker_state("a2a-1") is WorkerState.IDLE


def test_a2a_protobuf_message_payload_is_used_as_is() -> None:
    cluster, _, results = setup(Agent())
    message = new_text_message("say hi", role=pb.Role.ROLE_USER)
    cluster.aggregators["z1"].dispatch(
        "a2a-1", make_task("t1", message.SerializeToString(), content_type=A2A_MESSAGE_TYPE)
    )
    assert get_message_text(pb.Message.FromString(results[0].payload)) == "HI"


@pytest.mark.parametrize(
    ("mode", "status", "code"),
    [
        ("fail", TASK_STATUS_FAILED, "a2a_failed"),
        ("input", TASK_STATUS_FAILED, "a2a_input_required"),
        ("raise", TASK_STATUS_FAILED, "a2a_error"),
    ],
)
def test_failure_mappings(mode: str, status: int, code: str) -> None:
    cluster, _, results = setup(Agent())
    cluster.aggregators["z1"].dispatch("a2a-1", text_task("t1", mode))
    [result] = results
    assert result.status == status and result.error.code == code
    assert not result.error.retryable
    assert "boom" not in result.error.message  # only the exception type leaves the worker


def test_rejection_maps_to_non_retryable_rejected() -> None:
    cluster, _, results = setup(Agent())
    cluster.aggregators["z1"].dispatch("a2a-1", text_task("t1", "reject"))
    assert results[0].error.code == "a2a_rejected" and not results[0].error.retryable


def test_invalid_protobuf_payload_is_a_clean_failure() -> None:
    cluster, _, results = setup(Agent())
    cluster.aggregators["z1"].dispatch(
        "a2a-1", make_task("t1", b"\xff\xff\xff", content_type=A2A_MESSAGE_TYPE)
    )
    assert results[0].error.code == "invalid_payload"


def test_oversized_result_is_refused() -> None:
    class Huge(Agent):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            await event_queue.enqueue_event(new_text_message("x" * 70_000))

    cluster, _, results = setup(Huge())
    cluster.aggregators["z1"].dispatch("a2a-1", text_task("t1", "anything"))
    assert results[0].error.code == "result_too_large"


# --- asynchronous mode and cancellation --------------------------------------------------


def test_async_mode_defers_completion() -> None:
    async def scenario() -> list[TaskResult]:
        cluster, _, results = setup(Agent())
        agg = cluster.aggregators["z1"]
        agg.dispatch("a2a-1", text_task("t1", "work"))
        assert agg.worker_state("a2a-1") is WorkerState.EXECUTING
        for _ in range(100):
            if results:
                break
            await asyncio.sleep(0.02)
        assert agg.worker_state("a2a-1") is WorkerState.IDLE
        return results

    [result] = asyncio.run(scenario())
    assert result.status == TASK_STATUS_SUCCEEDED


def test_cancel_reaches_the_a2a_agent_and_reports_cancelled() -> None:
    agent = Agent()

    async def scenario() -> list[TaskResult]:
        cluster, bridge, results = setup(agent)
        agg = cluster.aggregators["z1"]
        agg.dispatch("a2a-1", text_task("t1", "slow"))
        for _ in range(100):  # wait until the framework has assigned an A2A task id
            if "t1" in bridge._a2a_ids:
                break
            await asyncio.sleep(0.02)
        assert agg.cancel("t1", "user abort")
        for _ in range(100):
            if agent.cancelled:
                break
            await asyncio.sleep(0.02)
        assert agg.worker_state("a2a-1") is WorkerState.IDLE
        return results

    [result] = asyncio.run(scenario())
    assert result.status == TASK_STATUS_CANCELLED
    assert len(agent.cancelled) == 1
