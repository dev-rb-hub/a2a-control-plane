"""CrewAI integration points for the a2a-control-plane lifecycle.

The integration adapts CrewAI workers and regional flows to the control plane's
identity, admission, liveness, task-dispatch, and state-reporting roles. These
interfaces are architectural scaffolding; transport and lifecycle behavior are
not implemented yet. Importing this optional integration requires CrewAI.
"""

from a2a_control_plane.integrations.crewai.agent import A2AWorkerAgent
from a2a_control_plane.integrations.crewai.flow import A2ARegionalAggregatorFlow

__all__ = ["A2ARegionalAggregatorFlow", "A2AWorkerAgent"]
