//! Subject grammar, NATS-style wildcard matching and role-based authorization (spec 02, 6).
//!
//! Authorization is derived from the authenticated SPIFFE ID only.

use std::fmt;

use crate::identity::{Role, SpiffeId};
use crate::tokens::is_valid_token;

pub const PREFIX: &str = "a2a";
/// What the CCR subscribes to for zone reports.
pub const REGISTRY_AGGREGATOR_STATE_PATTERN: &str = "a2a.zone.*.aggregator.state";
/// What the CCR may subscribe to for diagnostics only.
pub const REGISTRY_DIAGNOSTIC_PATTERN: &str = "a2a.zone.>";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Channel {
    Tasks,
    State,
    Results,
    Control,
}

impl Channel {
    pub fn as_str(self) -> &'static str {
        match self {
            Channel::Tasks => "tasks",
            Channel::State => "state",
            Channel::Results => "results",
            Channel::Control => "control",
        }
    }

    pub fn parse(value: &str) -> Option<Channel> {
        match value {
            "tasks" => Some(Channel::Tasks),
            "state" => Some(Channel::State),
            "results" => Some(Channel::Results),
            "control" => Some(Channel::Control),
            _ => None,
        }
    }
}

/// A concrete (wildcard-free) subject. `agent_id` is `None` for aggregator subjects.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Subject {
    pub zone_id: String,
    pub agent_id: Option<String>,
    pub channel: Channel,
}

impl Subject {
    /// Parse `a2a.zone.<zone>.agent.<agent>.<channel>` or `a2a.zone.<zone>.aggregator.<channel>`.
    pub fn parse(subject: &str) -> Option<Subject> {
        let parts: Vec<&str> = subject.split('.').collect();
        let (zone, agent, channel) = match parts.as_slice() {
            [PREFIX, "zone", zone, "agent", agent, channel] => (*zone, Some(*agent), *channel),
            [PREFIX, "zone", zone, "aggregator", channel] => (*zone, None, *channel),
            _ => return None,
        };
        if !is_valid_token(zone) || agent.is_some_and(|a| !is_valid_token(a)) {
            return None;
        }
        Some(Subject {
            zone_id: zone.to_owned(),
            agent_id: agent.map(str::to_owned),
            channel: Channel::parse(channel)?,
        })
    }
}

impl fmt::Display for Subject {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.agent_id {
            None => write!(
                f,
                "{PREFIX}.zone.{}.aggregator.{}",
                self.zone_id,
                self.channel.as_str()
            ),
            Some(agent) => write!(
                f,
                "{PREFIX}.zone.{}.agent.{agent}.{}",
                self.zone_id,
                self.channel.as_str()
            ),
        }
    }
}

pub fn agent_subject(zone_id: &str, agent_id: &str, channel: Channel) -> String {
    format!(
        "{PREFIX}.zone.{zone_id}.agent.{agent_id}.{}",
        channel.as_str()
    )
}

pub fn aggregator_subject(zone_id: &str, channel: Channel) -> String {
    format!("{PREFIX}.zone.{zone_id}.aggregator.{}", channel.as_str())
}

/// True if `pattern` is a legal subject or wildcard pattern: tokens, `*`, and `>` only last.
pub fn is_valid_pattern(pattern: &str) -> bool {
    let tokens: Vec<&str> = pattern.split('.').collect();
    tokens.iter().enumerate().all(|(i, token)| match *token {
        "*" => true,
        ">" => i == tokens.len() - 1,
        t => is_valid_token(t),
    })
}

/// NATS semantics: `*` matches one token, `>` matches one or more trailing tokens. Returns `None`
/// for an invalid pattern.
pub fn subject_matches(pattern: &str, subject: &str) -> Option<bool> {
    if !is_valid_pattern(pattern) {
        return None;
    }
    let p: Vec<&str> = pattern.split('.').collect();
    let s: Vec<&str> = subject.split('.').collect();
    for (i, token) in p.iter().enumerate() {
        if *token == ">" {
            return Some(s.len() > i);
        }
        match s.get(i) {
            None => return Some(false),
            Some(st) if *token != "*" && token != st => return Some(false),
            _ => {}
        }
    }
    Some(p.len() == s.len())
}

/// Whether `identity` may subscribe to `pattern` (spec 02, section 6.3).
pub fn may_subscribe(identity: &SpiffeId, pattern: &str) -> bool {
    if !is_valid_pattern(pattern) {
        return false;
    }
    match identity.role() {
        Role::Worker => Subject::parse(pattern).is_some_and(|s| {
            Some(s.zone_id.as_str()) == identity.zone_id()
                && s.agent_id.as_deref() == identity.agent_id()
                && matches!(s.channel, Channel::Tasks | Channel::Control)
        }),
        Role::Aggregator => {
            let t: Vec<&str> = pattern.split('.').collect();
            t.len() == 6
                && t[0] == PREFIX
                && t[1] == "zone"
                && Some(t[2]) == identity.zone_id()
                && t[3] == "agent"
                && (t[5] == "state" || t[5] == "results")
        }
        Role::Registry => {
            pattern == REGISTRY_AGGREGATOR_STATE_PATTERN || pattern == REGISTRY_DIAGNOSTIC_PATTERN
        }
        Role::Client => false,
    }
}

/// Whether `identity` may publish to `subject` (spec 02, sections 4 and 6.3).
pub fn may_publish(identity: &SpiffeId, subject: &str) -> bool {
    let Some(parsed) = Subject::parse(subject) else {
        return false;
    };
    if Some(parsed.zone_id.as_str()) != identity.zone_id() {
        return false;
    }
    match identity.role() {
        Role::Worker => {
            parsed.agent_id.as_deref() == identity.agent_id()
                && matches!(parsed.channel, Channel::State | Channel::Results)
        }
        Role::Aggregator => match parsed.agent_id {
            Some(_) => matches!(parsed.channel, Channel::Tasks | Channel::Control),
            None => parsed.channel == Channel::State,
        },
        Role::Registry | Role::Client => false,
    }
}
