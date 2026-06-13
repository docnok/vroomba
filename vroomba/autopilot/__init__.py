"""Autopilot registry."""

from vroomba.autopilot.polyphemus import PolyphemusAutopilot
from vroomba.autopilot.tiresias import TiresiasAutopilot

AUTOPILOTS: dict[str, type] = {
    "tiresias": TiresiasAutopilot,
    "polyphemus": PolyphemusAutopilot,
}


def get_autopilot(name: str):
    cls = AUTOPILOTS.get(name)
    if cls is None:
        raise ValueError(f"Unknown autopilot: {name!r}. Available: {list(AUTOPILOTS)}")
    return cls()
