"""Autopilot registry."""

from vroomba.autopilot.homer import HomerAutopilot

AUTOPILOTS: dict[str, type] = {
    "homer": HomerAutopilot,
}


def get_autopilot(name: str):
    cls = AUTOPILOTS.get(name)
    if cls is None:
        raise ValueError(f"Unknown autopilot: {name!r}. Available: {list(AUTOPILOTS)}")
    return cls()
