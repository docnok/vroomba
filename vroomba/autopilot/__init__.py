"""Autopilot registry."""

from vroomba.autopilot.cyclops import CyclopsAutopilot
from vroomba.autopilot.tiresias import TiresiasAutopilot

AUTOPILOTS: dict[str, type] = {
    "tiresias": TiresiasAutopilot,
    "cyclops": CyclopsAutopilot,
}


def get_autopilot(name: str):
    cls = AUTOPILOTS.get(name)
    if cls is None:
        raise ValueError(f"Unknown autopilot: {name!r}. Available: {list(AUTOPILOTS)}")
    return cls()
