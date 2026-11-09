//! Adaptive heartbeat schedule and liveness tracking (spec 03, section 6).
//!
//! Usable on both sides of a connection: the Worker uses [`AdaptiveHeartbeat::next_due`] to decide
//! when to send, the Aggregator uses [`AdaptiveHeartbeat::is_degraded`] on what it receives. Times
//! are monotonic seconds passed in by the caller.

/// Parameters were rejected: they require `0 < base <= max` and `miss_multiplier >= 1`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InvalidHeartbeatParams;

#[derive(Clone, Debug, PartialEq)]
pub struct AdaptiveHeartbeat {
    base_interval: f64,
    max_interval: f64,
    miss_multiplier: u32,
    interval: f64,
    last_message_at: Option<f64>,
}

fn valid(base: f64, max: f64, multiplier: u32) -> bool {
    0.0 < base && base <= max && multiplier >= 1
}

impl Default for AdaptiveHeartbeat {
    /// The defaults of spec 05, section 1.1: 5 s, 60 s, multiplier 3.
    fn default() -> Self {
        Self::new(5.0, 60.0, 3).expect("defaults are valid")
    }
}

impl AdaptiveHeartbeat {
    pub fn new(
        base_interval: f64,
        max_interval: f64,
        miss_multiplier: u32,
    ) -> Result<Self, InvalidHeartbeatParams> {
        if !valid(base_interval, max_interval, miss_multiplier) {
            return Err(InvalidHeartbeatParams);
        }
        Ok(AdaptiveHeartbeat {
            base_interval,
            max_interval,
            miss_multiplier,
            interval: base_interval,
            last_message_at: None,
        })
    }

    pub fn base_interval(&self) -> f64 {
        self.base_interval
    }

    pub fn max_interval(&self) -> f64 {
        self.max_interval
    }

    pub fn miss_multiplier(&self) -> u32 {
        self.miss_multiplier
    }

    /// The wait that applies after the most recent message.
    pub fn interval(&self) -> f64 {
        self.interval
    }

    pub fn last_message_at(&self) -> Option<f64> {
        self.last_message_at
    }

    /// Apply a `HeartbeatConfig` (spec 03, section 8). `None` or zero leaves a parameter
    /// unchanged. Returns `false` and changes nothing if the result would be invalid.
    pub fn configure(
        &mut self,
        base_interval: Option<f64>,
        max_interval: Option<f64>,
        miss_multiplier: Option<u32>,
    ) -> bool {
        let base = base_interval
            .filter(|v| *v != 0.0)
            .unwrap_or(self.base_interval);
        let max = max_interval
            .filter(|v| *v != 0.0)
            .unwrap_or(self.max_interval);
        let multiplier = miss_multiplier
            .filter(|v| *v != 0)
            .unwrap_or(self.miss_multiplier);
        if !valid(base, max, multiplier) {
            return false;
        }
        self.base_interval = base;
        self.max_interval = max;
        self.miss_multiplier = multiplier;
        self.interval = self.interval.max(base).min(max);
        true
    }

    /// A delta with changed capabilities was sent or received. It also counts as liveness.
    pub fn record_state_change(&mut self, now: f64) {
        self.interval = self.base_interval;
        self.last_message_at = Some(now);
    }

    /// A heartbeat was sent or received: back off for the next one.
    pub fn record_heartbeat(&mut self, now: f64) {
        self.interval = (self.interval * 2.0).min(self.max_interval);
        self.last_message_at = Some(now);
    }

    /// When the Worker must next send a message. `None` before the first message.
    pub fn next_due(&self) -> Option<f64> {
        self.last_message_at.map(|t| t + self.interval)
    }

    /// Time after which the peer has missed liveness. `None` before the first message.
    pub fn deadline(&self) -> Option<f64> {
        self.last_message_at
            .map(|t| t + f64::from(self.miss_multiplier) * self.interval)
    }

    pub fn is_degraded(&self, now: f64) -> bool {
        self.deadline().is_some_and(|deadline| now > deadline)
    }
}
