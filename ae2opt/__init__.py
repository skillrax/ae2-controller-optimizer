"""AE2 1.12.2 ME controller / P2P build simulator and optimizer.

See MECHANICS.md for the game rules this package implements and PLAN.md for
the overall design.
"""

__version__ = "1.1.0"

from .profile import DEFAULT_PROFILE, Profile, ProfileError, ProfileStore
from .geometry import Direction
from .layout import (
    CableAnchor,
    CableBus,
    CableType,
    Controller,
    DeviceBlock,
    DevicePart,
    EnergyAcceptor,
    Layout,
    LayoutError,
    P2PTunnel,
    TunnelPort,
)
from .sim import SimResult, simulate
from .rules import Report, Violation, check

__all__ = [
    "DEFAULT_PROFILE", "Profile", "ProfileError", "ProfileStore",
    "Direction",
    "CableAnchor", "CableBus", "CableType", "Controller", "DeviceBlock",
    "DevicePart", "EnergyAcceptor", "Layout", "LayoutError", "P2PTunnel", "TunnelPort",
    "SimResult", "simulate",
    "Report", "Violation", "check",
]
