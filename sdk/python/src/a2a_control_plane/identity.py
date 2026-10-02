"""SPIFFE identities for the three roles (spec 01, section 2.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from a2a_control_plane.tokens import validate_token

_SCHEME = "spiffe://"
_TRUST_DOMAIN_RE = re.compile(r"[a-z0-9._-]{1,255}")


class Role(StrEnum):
    REGISTRY = "registry"
    AGGREGATOR = "aggregator"
    WORKER = "worker"


@dataclass(frozen=True, slots=True)
class SpiffeId:
    """A role-scoped SPIFFE ID.

    Forms:
        spiffe://<td>/registry
        spiffe://<td>/zone/<zone_id>/aggregator
        spiffe://<td>/zone/<zone_id>/agent/<agent_id>
    """

    trust_domain: str
    role: Role
    zone_id: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if not _TRUST_DOMAIN_RE.fullmatch(self.trust_domain):
            raise ValueError(f"invalid trust domain {self.trust_domain!r}")
        if self.role is Role.REGISTRY:
            if self.zone_id is not None or self.agent_id is not None:
                raise ValueError("registry identity has no zone or agent")
        elif self.role is Role.AGGREGATOR:
            if self.zone_id is None or self.agent_id is not None:
                raise ValueError("aggregator identity requires a zone and no agent")
        elif self.zone_id is None or self.agent_id is None:
            raise ValueError("worker identity requires a zone and an agent")
        if self.zone_id is not None:
            validate_token(self.zone_id, "zone_id")
        if self.agent_id is not None:
            validate_token(self.agent_id, "agent_id")

    @classmethod
    def registry(cls, trust_domain: str) -> SpiffeId:
        return cls(trust_domain, Role.REGISTRY)

    @classmethod
    def aggregator(cls, trust_domain: str, zone_id: str) -> SpiffeId:
        return cls(trust_domain, Role.AGGREGATOR, zone_id)

    @classmethod
    def worker(cls, trust_domain: str, zone_id: str, agent_id: str) -> SpiffeId:
        return cls(trust_domain, Role.WORKER, zone_id, agent_id)

    @classmethod
    def parse(cls, uri: str) -> SpiffeId:
        if not uri.startswith(_SCHEME):
            raise ValueError(f"not a SPIFFE ID: {uri!r}")
        trust_domain, sep, path = uri[len(_SCHEME) :].partition("/")
        if not sep:
            raise ValueError(f"SPIFFE ID has no path: {uri!r}")
        parts = path.split("/")
        match parts:
            case ["registry"]:
                return cls.registry(trust_domain)
            case ["zone", zone_id, "aggregator"]:
                return cls.aggregator(trust_domain, zone_id)
            case ["zone", zone_id, "agent", agent_id]:
                return cls.worker(trust_domain, zone_id, agent_id)
        raise ValueError(f"unrecognized SPIFFE ID path: {uri!r}")

    def __str__(self) -> str:
        base = f"{_SCHEME}{self.trust_domain}"
        if self.role is Role.REGISTRY:
            return f"{base}/registry"
        if self.role is Role.AGGREGATOR:
            return f"{base}/zone/{self.zone_id}/aggregator"
        return f"{base}/zone/{self.zone_id}/agent/{self.agent_id}"
