# a2a-control-plane Specification 01: Identity

**Status:** Draft v0.1.0  
**Scope:** Workload identity, mTLS requirements, and credential lifecycle.

## 1. Conventions

The key words "MUST", "MUST NOT", "REQUIRED", "SHOULD", "SHOULD NOT", "RECOMMENDED", and "MAY" in this document are to be interpreted as described in BCP 14 ([RFC 2119], [RFC 8174]) when, and only when, they appear in all capitals.

## 2. Identity Model

Every participant (Registry, Aggregator, Worker, Client) MUST hold a [SPIFFE] identity delivered as an X.509-SVID. Implementations SHOULD use [SPIRE] as the SPIFFE implementation.

### 2.1 SPIFFE ID Format

| Role | SPIFFE ID |
|------|-----------|
| Central Cluster Registry | `spiffe://<trust-domain>/registry` |
| Regional Aggregator | `spiffe://<trust-domain>/zone/<zone_id>/aggregator` |
| Worker Agent | `spiffe://<trust-domain>/zone/<zone_id>/agent/<agent_id>` |
| Client | `spiffe://<trust-domain>/client/<client_id>` |

- `zone_id`, `agent_id` and `client_id` MUST satisfy the token grammar in [02-topology](02-topology.md) Section 6.1.
- A single trust domain MUST be used per cluster. Federation between clusters is out of scope for this version.
- An `agent_id` MUST be unique within the trust domain.
- The zone of a Worker or Aggregator is fixed by its SPIFFE ID, which SPIRE issues from the registration entry. The CCR records the zone ([02-topology](02-topology.md) Section 4.1); it does not assign a different one.
- All instances of a zone's Aggregator share one SPIFFE ID. They are told apart by the `instance_id` they present when attaching ([04-control-api](04-control-api.md) Section 3), which is not an identity and carries no authority.

## 3. Attestation

- Workers MUST be attested by the node or workload attestor of the local SPIRE agent before receiving an SVID.
- Registration entries for Workers MUST be created by the CCR operator or an automated controller with CCR authorization. Workers MUST NOT be able to create their own entries.
- Registration entries for Aggregators and Clients MUST be created only by the CCR operator or an automated controller with CCR authorization, and a zone MUST be provisioned at the CCR before an Aggregator of that zone can attach. Possession of a Client identity grants nothing by itself: the CCR's authorization policy decides what a client may request ([04-control-api](04-control-api.md) Section 4.1).
- Attestation selectors SHOULD bind the SPIFFE ID to infrastructure properties (for example namespace and service account on Kubernetes).

## 4. Mutual TLS

- All connections between components MUST use mutual TLS.
- TLS 1.3 MUST be used. Earlier versions MUST NOT be negotiated.
- Peers MUST validate the peer's certificate chain against the trust bundle and MUST validate the peer's SPIFFE ID against the expected role and zone for that connection.
- An Aggregator MUST reject a Worker whose SPIFFE ID zone component differs from the Aggregator's own zone.
- The CCR MUST reject an Aggregator whose SPIFFE ID does not have the Aggregator form or whose zone has not been provisioned.
- An Aggregator MUST accept a task submission only from a peer whose SPIFFE ID has the Client form and the Aggregator's trust domain, and MUST compare the voucher's client identity with that SPIFFE ID ([04-control-api](04-control-api.md) Section 4.4).
- Authorization decisions MUST be based on the SPIFFE ID in the URI SAN, not on the certificate subject or any client-supplied field.

## 5. Credential Lifecycle

| Parameter | Requirement |
|-----------|-------------|
| X.509-SVID lifetime | MUST NOT exceed 1 hour. |
| Rotation | Components MUST begin renewal no later than 50% of the SVID lifetime. |
| Rotation behavior | A connection MAY continue to carry traffic after the SVID it was established with is renewed, but MUST be replaced by a new connection that uses the renewed SVID before the old SVID expires. The replacement of a Worker's connection is a new session ([04-control-api](04-control-api.md) Section 2.6): the Worker registers again, and tasks in flight on the old session are reassigned or failed ([05-operations](05-operations.md) Section 6). |
| Trust bundle updates | Components MUST accept bundle updates without restart. |
| Expiry | A component whose SVID expires without renewal MUST stop sending traffic, and its peers MUST treat it as `Unregistered` (see [02-topology](02-topology.md) T10). |

## 6. Registration Challenge

In addition to transport authentication, admission of a Worker to the cluster uses a challenge-response exchange. The CCR is the root of this exchange and issues and verifies the challenge. The Aggregator relays it. The full exchange, including what the Worker signs, is defined in [04-control-api](04-control-api.md) Section 2.

In outline:

1. The Worker connects to its Aggregator over mTLS and sends a registration request containing its requested capability set and the versions and features it supports.
2. The Aggregator relays the request to the CCR. The CCR issues a challenge containing a single-use nonce of at least 128 bits, bound to the Worker's SPIFFE ID.
3. The Worker signs the nonce, together with its identity, a digest of the request and the TLS channel binding, with the private key of its current X.509-SVID, and returns the signature.
4. The CCR verifies the signature against the SVID presented during the handshake. On success the Worker is admitted.

Nonces MUST expire after the challenge timeout and MUST NOT be reused.

## 7. Revocation

- The CCR MUST support revoking the identity of a Worker or an Aggregator ([04-control-api](04-control-api.md) Section 6). Revoking a Worker MUST cause the SPIRE registration entry to be removed and MUST cause its Aggregator to close that Worker's connection. Revoking an Aggregator MUST cause every instance of that zone to be detached.
- Because SVIDs are short lived, revocation MUST take effect within one SVID lifetime at the latest, and SHOULD take effect immediately through active connection termination.
- Every revocation MUST be written to the audit log ([05-operations](05-operations.md) Section 7.1).

## 8. Security Considerations

- Private keys MUST NOT leave the process or SPIRE agent that generated them.
- Short SVID lifetimes limit the value of stolen credentials.
- Binding the zone into the SPIFFE ID prevents a compromised Worker from joining another zone.
- The effect of a compromise of each component is analysed in [06-threat-model](06-threat-model.md).

[RFC 2119]: https://www.rfc-editor.org/rfc/rfc2119
[RFC 8174]: https://www.rfc-editor.org/rfc/rfc8174
[SPIFFE]: https://spiffe.io
[SPIRE]: https://spiffe.io/spire/
