"""Subject grammar, NATS-style wildcard matching, and role-based authorization.

Implements spec 02, section 6. Authorization is derived from the authenticated SPIFFE ID only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from a2a_control_plane.identity import Role, SpiffeId
from a2a_control_plane.tokens import is_valid_token, validate_token

PREFIX = "a2a"

REGISTRY_AGGREGATOR_STATE_PATTERN = "a2a.zone.*.aggregator.state"
REGISTRY_DIAGNOSTIC_PATTERN = "a2a.zone.>"


class Channel(StrEnum):
    TASKS = "tasks"
    STATE = "state"
    RESULTS = "results"
    CONTROL = "control"


@dataclass(frozen=True, slots=True)
class Subject:
    """A concrete (wildcard-free) subject.

    ``agent_id`` is None for aggregator subjects.
    """

    zone_id: str
    channel: Channel
    agent_id: str | None = None

    def __post_init__(self) -> None:
        validate_token(self.zone_id, "zone_id")
        if self.agent_id is not None:
            validate_token(self.agent_id, "agent_id")

    @classmethod
    def parse(cls, subject: str) -> Subject:
        parts = subject.split(".")
        match parts:
            case [_, "zone", zone_id, "agent", agent_id, channel] if parts[0] == PREFIX:
                return cls(zone_id, Channel(channel), agent_id)
            case [_, "zone", zone_id, "aggregator", channel] if parts[0] == PREFIX:
                return cls(zone_id, Channel(channel))
        raise ValueError(f"not a concrete a2a subject: {subject!r}")

    def __str__(self) -> str:
        if self.agent_id is None:
            return f"{PREFIX}.zone.{self.zone_id}.aggregator.{self.channel}"
        return f"{PREFIX}.zone.{self.zone_id}.agent.{self.agent_id}.{self.channel}"


def agent_subject(zone_id: str, agent_id: str, channel: Channel) -> str:
    return str(Subject(zone_id, channel, agent_id))


def aggregator_subject(zone_id: str, channel: Channel) -> str:
    return str(Subject(zone_id, channel))


def validate_pattern(pattern: str) -> None:
    """Raise ValueError unless ``pattern`` is a legal subject or wildcard pattern."""
    tokens = pattern.split(".")
    for i, token in enumerate(tokens):
        if token == "*":
            continue
        if token == ">":
            if i != len(tokens) - 1:
                raise ValueError(f"'>' must be the last token: {pattern!r}")
            continue
        if not is_valid_token(token):
            raise ValueError(f"invalid token {token!r} in pattern {pattern!r}")


def subject_matches(pattern: str, subject: str) -> bool:
    """NATS semantics: ``*`` matches one token, ``>`` matches one or more trailing tokens."""
    validate_pattern(pattern)
    p_tokens = pattern.split(".")
    s_tokens = subject.split(".")
    for i, token in enumerate(p_tokens):
        if token == ">":
            return len(s_tokens) > i
        if i >= len(s_tokens):
            return False
        if token != "*" and token != s_tokens[i]:
            return False
    return len(p_tokens) == len(s_tokens)


def _concrete(value: str) -> Subject | None:
    try:
        return Subject.parse(value)
    except ValueError:
        return None


def may_subscribe(identity: SpiffeId, pattern: str) -> bool:
    """Spec 02, section 6.3."""
    try:
        validate_pattern(pattern)
    except ValueError:
        return False

    if identity.role is Role.WORKER:
        subject = _concrete(pattern)  # workers MUST NOT use wildcards
        return (
            subject is not None
            and subject.zone_id == identity.zone_id
            and subject.agent_id == identity.agent_id
            and subject.channel in (Channel.TASKS, Channel.CONTROL)
        )

    if identity.role is Role.AGGREGATOR:
        tokens = pattern.split(".")
        return (
            len(tokens) == 6
            and tokens[:2] == [PREFIX, "zone"]
            and tokens[2] == identity.zone_id
            and tokens[3] == "agent"
            and tokens[5] in (Channel.STATE, Channel.RESULTS)
        )

    return pattern in (REGISTRY_AGGREGATOR_STATE_PATTERN, REGISTRY_DIAGNOSTIC_PATTERN)


def may_publish(identity: SpiffeId, subject: str) -> bool:
    """Publishing is restricted to the subjects each role owns (spec 02, sections 4 and 6.2)."""
    parsed = _concrete(subject)
    if parsed is None or parsed.zone_id != identity.zone_id:
        return False

    if identity.role is Role.WORKER:
        return parsed.agent_id == identity.agent_id and parsed.channel in (
            Channel.STATE,
            Channel.RESULTS,
        )

    if identity.role is Role.AGGREGATOR:
        if parsed.agent_id is not None:
            return parsed.channel in (Channel.TASKS, Channel.CONTROL)
        return parsed.channel is Channel.STATE

    return False  # the registry uses gRPC, not the regional bus
