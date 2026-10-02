from __future__ import annotations

import pytest

from a2a_control_plane.identity import SpiffeId
from a2a_control_plane.subjects import (
    Channel,
    Subject,
    agent_subject,
    aggregator_subject,
    may_publish,
    may_subscribe,
    subject_matches,
)

TD = "example.org"
WORKER = SpiffeId.worker(TD, "z1", "w1")
AGG = SpiffeId.aggregator(TD, "z1")
REGISTRY = SpiffeId.registry(TD)


def test_subject_round_trip() -> None:
    s = agent_subject("z1", "w1", Channel.TASKS)
    assert s == "a2a.zone.z1.agent.w1.tasks"
    assert Subject.parse(s) == Subject("z1", Channel.TASKS, "w1")
    assert aggregator_subject("z1", Channel.STATE) == "a2a.zone.z1.aggregator.state"


@pytest.mark.parametrize(
    "bad",
    ["a2a.zone.z1.agent.*.tasks", "a2a.zone.z1.agent.w1.bogus", "x.zone.z1.agent.w1.tasks", "a2a"],
)
def test_subject_parse_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        Subject.parse(bad)


@pytest.mark.parametrize(
    ("pattern", "subject", "expected"),
    [
        ("a2a.zone.z1.agent.*.state", "a2a.zone.z1.agent.w1.state", True),
        ("a2a.zone.z1.agent.*.state", "a2a.zone.z2.agent.w1.state", False),
        ("a2a.zone.z1.agent.*.state", "a2a.zone.z1.agent.w1.tasks", False),
        ("a2a.zone.>", "a2a.zone.z1.aggregator.state", True),
        ("a2a.zone.>", "a2a.zone", False),
        ("a2a.zone.*.aggregator.state", "a2a.zone.z9.aggregator.state", True),
        ("a2a.zone.*", "a2a.zone.z1.aggregator.state", False),
    ],
)
def test_wildcards(pattern: str, subject: str, expected: bool) -> None:
    assert subject_matches(pattern, subject) is expected


def test_gt_must_be_last() -> None:
    with pytest.raises(ValueError):
        subject_matches("a2a.>.state", "a2a.x.state")


# --- spec 02, section 6.3 -------------------------------------------------------------------


def test_worker_subscriptions() -> None:
    assert may_subscribe(WORKER, "a2a.zone.z1.agent.w1.tasks")
    assert may_subscribe(WORKER, "a2a.zone.z1.agent.w1.control")
    assert not may_subscribe(WORKER, "a2a.zone.z1.agent.w2.tasks")  # other agent
    assert not may_subscribe(WORKER, "a2a.zone.z2.agent.w1.tasks")  # other zone
    assert not may_subscribe(WORKER, "a2a.zone.z1.agent.w1.state")  # not an inbound channel
    assert not may_subscribe(WORKER, "a2a.zone.z1.agent.*.tasks")  # no wildcards
    assert not may_subscribe(WORKER, "a2a.zone.>")


def test_aggregator_subscriptions_stay_in_zone() -> None:
    assert may_subscribe(AGG, "a2a.zone.z1.agent.*.state")
    assert may_subscribe(AGG, "a2a.zone.z1.agent.*.results")
    assert may_subscribe(AGG, "a2a.zone.z1.agent.w1.state")
    assert not may_subscribe(AGG, "a2a.zone.z2.agent.*.state")
    assert not may_subscribe(AGG, "a2a.zone.*.agent.*.state")
    assert not may_subscribe(AGG, "a2a.zone.>")
    assert not may_subscribe(AGG, "a2a.zone.z1.agent.*.tasks")


def test_registry_subscriptions() -> None:
    assert may_subscribe(REGISTRY, "a2a.zone.*.aggregator.state")
    assert may_subscribe(REGISTRY, "a2a.zone.>")
    assert not may_subscribe(REGISTRY, "a2a.zone.*.agent.*.tasks")


def test_invalid_pattern_is_denied_not_raised() -> None:
    assert not may_subscribe(REGISTRY, "a2a..>")


def test_publish_rules() -> None:
    assert may_publish(WORKER, "a2a.zone.z1.agent.w1.state")
    assert may_publish(WORKER, "a2a.zone.z1.agent.w1.results")
    assert not may_publish(WORKER, "a2a.zone.z1.agent.w2.state")  # impersonation
    assert not may_publish(WORKER, "a2a.zone.z1.agent.w1.tasks")  # outbound-only channel
    assert not may_publish(WORKER, "a2a.zone.z1.aggregator.state")

    assert may_publish(AGG, "a2a.zone.z1.agent.w1.tasks")
    assert may_publish(AGG, "a2a.zone.z1.agent.w1.control")
    assert may_publish(AGG, "a2a.zone.z1.aggregator.state")
    assert not may_publish(AGG, "a2a.zone.z2.agent.w1.tasks")  # other zone
    assert not may_publish(AGG, "a2a.zone.z1.agent.w1.state")

    assert not may_publish(REGISTRY, "a2a.zone.z1.aggregator.state")
