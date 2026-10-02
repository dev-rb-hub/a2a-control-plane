"""CrewAI Worker Agent integration scaffold.

The planned adapter maps a CrewAI agent into the Worker lifecycle in spec 02,
while reusing the identity and protocol requirements in specs 01 and 03.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from crewai import Agent

from a2a_control_plane.identity import SpiffeId


class SVIDSigner(Protocol):
    """Signing interface backed by a SPIFFE/SPIRE-managed private key."""

    def sign(self, payload: bytes) -> bytes:
        """Sign a registration challenge without exposing the private key."""
        ...


class A2AWorkerAgent(Agent):
    """CrewAI Agent scaffold for a single a2a-control-plane Worker identity.

    ``nats_servers`` records the zonal broker configuration for the eventual
    JetStream-backed task adapter. Per spec 03, Workers must not connect to the
    regional bus directly; a future implementation must keep NATS access behind
    the Regional Aggregator or its authorized bridge.
    """

    def __init__(
        self,
        *,
        identity: SpiffeId,
        nats_servers: Sequence[str],
        svid_signer: SVIDSigner,
        role: str,
        goal: str,
        backstory: str,
        **agent_options: Any,
    ) -> None:
        """Initialize CrewAI parameters and retain control-plane configuration.

        TODO: Validate that ``identity`` is a Worker SPIFFE ID containing both
        ``zone_id`` and ``agent_id``. Treat the broker addresses as adapter
        configuration, not permission for this Worker to bypass its Aggregator.
        Preserve all supported CrewAI Agent options through ``agent_options``.
        """
        super().__init__(
            role=role,
            goal=goal,
            backstory=backstory,
            **agent_options,
        )
        self.identity = identity
        self.nats_servers = tuple(nats_servers)
        self._svid_signer = svid_signer

    async def register(self) -> None:
        """Perform the proxied challenge-response admission handshake.

        TODO: Establish the Worker's one long-lived mTLS connection to an
        Aggregator in its own zone and authenticate the peer's SPIFFE identity.
        Send the requested capability set through the Aggregator; receive the
        CCR-issued, single-use nonce (at least 128 bits) bound to this Worker's
        SPIFFE ID; sign it with the current SVID through ``svid_signer``; and
        return the response through the Aggregator for CCR verification.
        Respect the challenge timeout, invalidate failed or expired attempts,
        and publish the initial complete capability delta only after admission.
        The Worker must not open a separate connection to the CCR.
        """
        raise NotImplementedError

    async def heartbeat_loop(self) -> None:
        """Maintain liveness using the spec-defined adaptive heartbeat schedule.

        TODO: Start with the configured 5-second base interval, double after
        each heartbeat without an intervening state change, and cap at
        60 seconds. Any state delta also counts as liveness and resets the
        interval; do not emit a separate heartbeat during that interval.
        Coordinate the current interval with the Aggregator, whose missed
        deadline is the configured multiplier (default 3) times that interval.
        Stop sending on connection loss, revocation, or SVID expiry, and ensure
        renewal/reconnection follows the credential lifecycle in spec 01.
        """
        raise NotImplementedError

    async def consume_tasks(self) -> None:
        """Run CrewAI task execution from the zonal JetStream-backed task feed.

        TODO: Replace the planned in-memory task handoff with asynchronous
        consumption of the Worker's own ``tasks`` subject, retaining
        JetStream-backed durability/acknowledgement at the authorized
        Aggregator bridge. Do not let the Worker connect or subscribe directly
        to NATS: spec 02 restricts Workers to their own Aggregator, and spec 03
        requires the Aggregator to map the authenticated Worker stream to bus
        subjects. Validate task deadlines, execute the CrewAI adapter, publish
        exactly one protobuf TaskResult per accepted task, and acknowledge or
        reject the broker delivery only after the result outcome is known.
        """
        raise NotImplementedError
