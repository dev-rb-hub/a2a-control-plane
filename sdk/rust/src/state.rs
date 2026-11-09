//! Worker Agent state machine (spec 02, section 5).

/// Worker states (spec 02, section 5.1).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum WorkerState {
    Unregistered,
    Challenged,
    Registered,
    Idle,
    Executing,
    Degraded,
}

/// Events that drive the transitions T1 to T11 (spec 02, section 5.2).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum WorkerEvent {
    /// T1
    Connect,
    /// T2
    ChallengePassed,
    /// T3: invalid response or timeout.
    ChallengeFailed,
    /// T4
    InitialDeltaAccepted,
    /// T5
    TaskAccepted,
    /// T6
    TasksCompleted,
    /// T7: liveness missed or self-reported fault.
    Degrade,
    /// T8
    Recovered,
    /// T9
    GraceExpired,
    /// T10: identity revoked, SVID expired or session replaced.
    IdentityLost,
    /// T10: a drain ended (spec 03, section 8).
    Drained,
    /// T11: no initial snapshot after admission.
    RegistrationTimeout,
}

impl WorkerState {
    pub const ALL: [WorkerState; 6] = [
        WorkerState::Unregistered,
        WorkerState::Challenged,
        WorkerState::Registered,
        WorkerState::Idle,
        WorkerState::Executing,
        WorkerState::Degraded,
    ];

    /// The state name used in the specification.
    pub fn as_str(self) -> &'static str {
        match self {
            WorkerState::Unregistered => "Unregistered",
            WorkerState::Challenged => "Challenged",
            WorkerState::Registered => "Registered",
            WorkerState::Idle => "Idle",
            WorkerState::Executing => "Executing",
            WorkerState::Degraded => "Degraded",
        }
    }

    /// Only `Idle` and `Executing` Workers may receive tasks (spec 02, section 5.3).
    pub fn can_receive_tasks(self) -> bool {
        matches!(self, WorkerState::Idle | WorkerState::Executing)
    }
}

impl WorkerEvent {
    pub const ALL: [WorkerEvent; 12] = [
        WorkerEvent::Connect,
        WorkerEvent::ChallengePassed,
        WorkerEvent::ChallengeFailed,
        WorkerEvent::InitialDeltaAccepted,
        WorkerEvent::TaskAccepted,
        WorkerEvent::TasksCompleted,
        WorkerEvent::Degrade,
        WorkerEvent::Recovered,
        WorkerEvent::GraceExpired,
        WorkerEvent::IdentityLost,
        WorkerEvent::Drained,
        WorkerEvent::RegistrationTimeout,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            WorkerEvent::Connect => "connect",
            WorkerEvent::ChallengePassed => "challenge_passed",
            WorkerEvent::ChallengeFailed => "challenge_failed",
            WorkerEvent::InitialDeltaAccepted => "initial_delta_accepted",
            WorkerEvent::TaskAccepted => "task_accepted",
            WorkerEvent::TasksCompleted => "tasks_completed",
            WorkerEvent::Degrade => "degrade",
            WorkerEvent::Recovered => "recovered",
            WorkerEvent::GraceExpired => "grace_expired",
            WorkerEvent::IdentityLost => "identity_lost",
            WorkerEvent::RegistrationTimeout => "registration_timeout",
            WorkerEvent::Drained => "drained",
        }
    }
}

/// The target state, or `None` if the specification does not list the transition.
pub fn next_state(state: WorkerState, event: WorkerEvent) -> Option<WorkerState> {
    use WorkerEvent as E;
    use WorkerState as S;
    match (state, event) {
        (S::Unregistered, E::Connect) => Some(S::Challenged),
        (S::Challenged, E::ChallengePassed) => Some(S::Registered),
        (S::Challenged, E::ChallengeFailed) => Some(S::Unregistered),
        (S::Registered, E::InitialDeltaAccepted) => Some(S::Idle),
        (S::Idle, E::TaskAccepted) => Some(S::Executing),
        (S::Executing, E::TasksCompleted) => Some(S::Idle),
        (S::Idle | S::Executing, E::Degrade) => Some(S::Degraded),
        (S::Degraded, E::Recovered) => Some(S::Idle),
        (S::Degraded, E::GraceExpired) => Some(S::Unregistered),
        (S::Registered | S::Idle | S::Executing | S::Degraded, E::IdentityLost) => {
            Some(S::Unregistered)
        }
        (S::Registered, E::RegistrationTimeout) => Some(S::Unregistered),
        (S::Idle | S::Executing | S::Degraded, E::Drained) => Some(S::Unregistered),
        _ => None,
    }
}

/// A Worker's state. Unlisted transitions are ignored: [`WorkerStateMachine::apply`] returns
/// `false` and the state is unchanged (spec 02, section 5.2).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WorkerStateMachine {
    state: WorkerState,
}

impl Default for WorkerStateMachine {
    fn default() -> Self {
        Self::new(WorkerState::Unregistered)
    }
}

impl WorkerStateMachine {
    pub fn new(state: WorkerState) -> Self {
        WorkerStateMachine { state }
    }

    pub fn state(&self) -> WorkerState {
        self.state
    }

    pub fn can_receive_tasks(&self) -> bool {
        self.state.can_receive_tasks()
    }

    pub fn apply(&mut self, event: WorkerEvent) -> bool {
        match next_state(self.state, event) {
            Some(target) => {
                self.state = target;
                true
            }
            None => false,
        }
    }
}
