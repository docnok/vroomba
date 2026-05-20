"""Autopilot registry."""

from vroomba.autopilot.argus import ArgusAutopilot
from vroomba.autopilot.homer import HomerAutopilot

AUTOPILOTS: dict[str, type] = {
    "homer": HomerAutopilot,
    "argus": ArgusAutopilot,
}


def get_autopilot(name: str):
    cls = AUTOPILOTS.get(name)
    if cls is None:
        raise ValueError(f"Unknown autopilot: {name!r}. Available: {list(AUTOPILOTS)}")
    return cls()
