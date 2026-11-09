//! SPIFFE identities for the roles of the control plane (spec 01, section 2.1).
//!
//! Forms:
//! - `spiffe://<td>/registry`
//! - `spiffe://<td>/zone/<zone_id>/aggregator`
//! - `spiffe://<td>/zone/<zone_id>/agent/<agent_id>`
//! - `spiffe://<td>/client/<client_id>`

use std::fmt;

use crate::tokens::is_valid_token;

const SCHEME: &str = "spiffe://";

/// The role a SPIFFE ID grants.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Role {
    Registry,
    Aggregator,
    Worker,
    Client,
}

impl Role {
    pub fn as_str(self) -> &'static str {
        match self {
            Role::Registry => "registry",
            Role::Aggregator => "aggregator",
            Role::Worker => "worker",
            Role::Client => "client",
        }
    }
}

/// Why a SPIFFE ID was rejected.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InvalidSpiffeId(pub String);

impl fmt::Display for InvalidSpiffeId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid SPIFFE ID: {}", self.0)
    }
}

impl std::error::Error for InvalidSpiffeId {}

/// A role-scoped SPIFFE ID. Construct it with the role constructors or [`SpiffeId::parse`], which
/// validate every component, so a value of this type is always well formed.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct SpiffeId {
    trust_domain: String,
    role: Role,
    zone_id: Option<String>,
    agent_id: Option<String>,
    client_id: Option<String>,
}

fn valid_trust_domain(td: &str) -> bool {
    (1..=255).contains(&td.len())
        && td
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"._-".contains(&b))
}

fn check(ok: bool, what: &str, value: &str) -> Result<(), InvalidSpiffeId> {
    if ok {
        Ok(())
    } else {
        Err(InvalidSpiffeId(format!("invalid {what} {value:?}")))
    }
}

impl SpiffeId {
    fn new(
        trust_domain: &str,
        role: Role,
        zone_id: Option<&str>,
        agent_id: Option<&str>,
        client_id: Option<&str>,
    ) -> Result<Self, InvalidSpiffeId> {
        check(
            valid_trust_domain(trust_domain),
            "trust domain",
            trust_domain,
        )?;
        for (what, value) in [
            ("zone_id", zone_id),
            ("agent_id", agent_id),
            ("client_id", client_id),
        ] {
            if let Some(v) = value {
                check(is_valid_token(v), what, v)?;
            }
        }
        Ok(SpiffeId {
            trust_domain: trust_domain.to_owned(),
            role,
            zone_id: zone_id.map(str::to_owned),
            agent_id: agent_id.map(str::to_owned),
            client_id: client_id.map(str::to_owned),
        })
    }

    pub fn registry(trust_domain: &str) -> Result<Self, InvalidSpiffeId> {
        Self::new(trust_domain, Role::Registry, None, None, None)
    }

    pub fn aggregator(trust_domain: &str, zone_id: &str) -> Result<Self, InvalidSpiffeId> {
        Self::new(trust_domain, Role::Aggregator, Some(zone_id), None, None)
    }

    pub fn worker(
        trust_domain: &str,
        zone_id: &str,
        agent_id: &str,
    ) -> Result<Self, InvalidSpiffeId> {
        Self::new(
            trust_domain,
            Role::Worker,
            Some(zone_id),
            Some(agent_id),
            None,
        )
    }

    pub fn client(trust_domain: &str, client_id: &str) -> Result<Self, InvalidSpiffeId> {
        Self::new(trust_domain, Role::Client, None, None, Some(client_id))
    }

    /// Parse one of the four role-scoped forms. Anything else is rejected.
    pub fn parse(uri: &str) -> Result<Self, InvalidSpiffeId> {
        let rest = uri
            .strip_prefix(SCHEME)
            .ok_or_else(|| InvalidSpiffeId(format!("not a SPIFFE ID: {uri:?}")))?;
        let (trust_domain, path) = rest
            .split_once('/')
            .ok_or_else(|| InvalidSpiffeId(format!("SPIFFE ID has no path: {uri:?}")))?;
        let parts: Vec<&str> = path.split('/').collect();
        match parts.as_slice() {
            ["registry"] => Self::registry(trust_domain),
            ["zone", zone, "aggregator"] => Self::aggregator(trust_domain, zone),
            ["zone", zone, "agent", agent] => Self::worker(trust_domain, zone, agent),
            ["client", client] => Self::client(trust_domain, client),
            _ => Err(InvalidSpiffeId(format!(
                "unrecognized SPIFFE ID path: {uri:?}"
            ))),
        }
    }

    pub fn trust_domain(&self) -> &str {
        &self.trust_domain
    }

    pub fn role(&self) -> Role {
        self.role
    }

    pub fn zone_id(&self) -> Option<&str> {
        self.zone_id.as_deref()
    }

    pub fn agent_id(&self) -> Option<&str> {
        self.agent_id.as_deref()
    }

    pub fn client_id(&self) -> Option<&str> {
        self.client_id.as_deref()
    }
}

impl fmt::Display for SpiffeId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{SCHEME}{}", self.trust_domain)?;
        match (self.role, &self.zone_id, &self.agent_id, &self.client_id) {
            (Role::Registry, ..) => write!(f, "/registry"),
            (Role::Client, _, _, Some(c)) => write!(f, "/client/{c}"),
            (Role::Aggregator, Some(z), ..) => write!(f, "/zone/{z}/aggregator"),
            (Role::Worker, Some(z), Some(a), _) => write!(f, "/zone/{z}/agent/{a}"),
            _ => unreachable!("constructors guarantee the components of each role"),
        }
    }
}
