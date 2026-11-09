//! Token grammar shared by zone ids, agent ids and subject segments (spec 02, section 6.1).

/// True if `value` is 1 to 64 characters from `A-Z`, `a-z`, `0-9`, `-` and `_`.
pub fn is_valid_token(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
