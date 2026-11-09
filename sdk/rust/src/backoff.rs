//! Reconnect delay with full jitter (spec 05, section 6).

/// Parameters were rejected: they require `attempt >= 0`, `initial > 0`, `factor >= 1` and
/// `maximum >= initial`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InvalidBackoff;

/// Seconds to wait before reconnect `attempt` (0 for the first retry): `rng * min(maximum,
/// initial * factor^attempt)`. `rng` is a uniform sample from `[0, 1)` supplied by the caller, so
/// the function stays deterministic and testable.
pub fn reconnect_delay(
    attempt: i64,
    initial: f64,
    factor: f64,
    maximum: f64,
    rng: f64,
) -> Result<f64, InvalidBackoff> {
    if attempt < 0 || initial <= 0.0 || factor < 1.0 || maximum < initial {
        return Err(InvalidBackoff);
    }
    // Precision loss for huge attempts is irrelevant: the ceiling saturates at `maximum`.
    #[allow(clippy::cast_precision_loss)]
    let ceiling = (initial * factor.powf(attempt as f64)).min(maximum);
    Ok(rng * ceiling)
}
