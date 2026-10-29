from __future__ import annotations

import pytest

from a2a_control_plane.identity import Role, SpiffeId


def test_round_trip_all_roles() -> None:
    for uri in (
        "spiffe://example.org/registry",
        "spiffe://example.org/zone/eu-west-1/aggregator",
        "spiffe://example.org/zone/eu-west-1/agent/worker_7",
    ):
        assert str(SpiffeId.parse(uri)) == uri


def test_worker_fields() -> None:
    sid = SpiffeId.parse("spiffe://example.org/zone/z1/agent/a1")
    assert (sid.role, sid.zone_id, sid.agent_id) == (Role.WORKER, "z1", "a1")


@pytest.mark.parametrize(
    "uri",
    [
        "http://example.org/registry",
        "spiffe://example.org",
        "spiffe://example.org/registry/",
        "spiffe://example.org/zone/z1",
        "spiffe://example.org/zone/z1/agent",
        "spiffe://example.org/zone/z.1/agent/a1",
        "spiffe://example.org/zone/z1/agent/a1/extra",
        "spiffe://user@example.org/registry",
        "spiffe://example.org:8080/registry",
        "spiffe://Example.org/registry",
    ],
)
def test_rejects_malformed(uri: str) -> None:
    with pytest.raises(ValueError):
        SpiffeId.parse(uri)


def test_rejects_overlong_token() -> None:
    with pytest.raises(ValueError):
        SpiffeId.worker("example.org", "z1", "a" * 65)


def test_client_identity_round_trips() -> None:
    sid = SpiffeId.parse("spiffe://example.org/client/billing-ui")
    assert (sid.role, sid.client_id, sid.zone_id, sid.agent_id) == (
        Role.CLIENT,
        "billing-ui",
        None,
        None,
    )
    assert str(sid) == "spiffe://example.org/client/billing-ui"
    assert sid == SpiffeId.client("example.org", "billing-ui")


@pytest.mark.parametrize(
    "uri",
    [
        "spiffe://example.org/client",
        "spiffe://example.org/client/",
        "spiffe://example.org/client/a/b",
        "spiffe://example.org/client/a.b",
        "spiffe://example.org/zone/z1/client/c1",
    ],
)
def test_rejects_malformed_client_ids(uri: str) -> None:
    with pytest.raises(ValueError):
        SpiffeId.parse(uri)


def test_only_clients_carry_a_client_id() -> None:
    with pytest.raises(ValueError):
        SpiffeId("example.org", Role.WORKER, "z1", "a1", client_id="c1")
    with pytest.raises(ValueError):
        SpiffeId("example.org", Role.CLIENT, "z1", None, client_id="c1")
    with pytest.raises(ValueError):
        SpiffeId("example.org", Role.CLIENT)
