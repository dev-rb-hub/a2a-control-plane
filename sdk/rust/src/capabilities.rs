//! Capability maps: limits, reserved keys, diffs and matching (spec 03, section 5.2).

use std::collections::BTreeMap;

pub const MAX_CAPABILITIES: usize = 64;
pub const MAX_CAPABILITY_VALUE_BYTES: usize = 256;
pub const RESERVED_PREFIX: &str = "cp.";
pub const HEALTH_KEY: &str = "cp.health";
pub const HEALTH_OK: &str = "ok";
pub const HEALTH_FAULT: &str = "fault";

/// A capability map that breaks spec 03, section 5.2.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InvalidCapabilities(pub String);

fn valid_key(key: &str) -> bool {
    (1..=64).contains(&key.len())
        && key
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}

/// Check every key and value. `allow_removal` accepts empty values, which a delta uses to remove a
/// capability.
pub fn validate_capabilities<'a, I>(
    capabilities: I,
    allow_removal: bool,
) -> Result<(), InvalidCapabilities>
where
    I: IntoIterator<Item = (&'a str, &'a str)>,
{
    let mut count = 0;
    for (key, value) in capabilities {
        count += 1;
        if count > MAX_CAPABILITIES {
            return Err(InvalidCapabilities(format!(
                "more than {MAX_CAPABILITIES} capabilities"
            )));
        }
        if !valid_key(key) {
            return Err(InvalidCapabilities(format!(
                "invalid capability key {key:?}"
            )));
        }
        if value.len() > MAX_CAPABILITY_VALUE_BYTES {
            return Err(InvalidCapabilities(format!(
                "capability {key:?} value exceeds {MAX_CAPABILITY_VALUE_BYTES} bytes"
            )));
        }
        if key.starts_with(RESERVED_PREFIX) {
            let defined = key == HEALTH_KEY && (value == HEALTH_OK || value == HEALTH_FAULT);
            let removal = key == HEALTH_KEY && allow_removal && value.is_empty();
            if !defined && !removal {
                return Err(InvalidCapabilities(format!(
                    "reserved capability {key:?} has an undefined name or value"
                )));
            }
        } else if value.is_empty() && !allow_removal {
            return Err(InvalidCapabilities(
                "capability values must be non-empty; an empty value means removal".into(),
            ));
        }
    }
    Ok(())
}

/// True if every required key is advertised with exactly the required value.
pub fn capabilities_match(
    required: &BTreeMap<String, String>,
    advertised: &BTreeMap<String, String>,
) -> bool {
    required
        .iter()
        .all(|(key, value)| advertised.get(key) == Some(value))
}

/// Only what changed from `old` to `new`. A removed capability maps to the empty string. Fails if
/// `new` contains an empty value, since that would be indistinguishable from a removal.
pub fn diff_capabilities(
    old: &BTreeMap<String, String>,
    new: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>, InvalidCapabilities> {
    if new.values().any(String::is_empty) {
        return Err(InvalidCapabilities(
            "capability values must be non-empty; an empty value means removal".into(),
        ));
    }
    let mut changed: BTreeMap<String, String> = new
        .iter()
        .filter(|(key, value)| old.get(*key) != Some(*value))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    for key in old.keys().filter(|key| !new.contains_key(*key)) {
        changed.insert(key.clone(), String::new());
    }
    Ok(changed)
}
