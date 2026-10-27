"""Game-config profiles: the numbers every rule reads instead of constants.

The default profile (8 / 32 / 7x7x7, channels on) matches both stock AE2 rv6
and an untouched AE2 Unofficial Extended Life config, so the app works with no
setup. Users change it by manual input or by importing a config file
(see cfgimport.py); both paths produce a Profile.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

SCHEMA_VERSION = 1

# Limits enforced by AE2 UEL's AEConfig. Values outside them are allowed (other
# forks may differ) but produce a warning.
KNOWN_CAPACITY_MAX = 256
KNOWN_SIZE_MAX = 63


class ProfileError(ValueError):
    """A profile value that cannot mean anything (not an int, or < 1)."""


@dataclass(frozen=True)
class Profile:
    name: str = "Default (8/32/7x7x7)"
    normal_channel_capacity: int = 8
    dense_channel_capacity: int = 32
    max_controller_size: tuple[int, int, int] = (7, 7, 7)
    channels_enabled: bool = True
    # Free-form note on where the values came from (manual, imported file...).
    source: str = "built-in default"

    def __post_init__(self) -> None:
        for label, value in (
            ("normal_channel_capacity", self.normal_channel_capacity),
            ("dense_channel_capacity", self.dense_channel_capacity),
            *(("max_controller_size", v) for v in self.max_controller_size),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProfileError(f"{label} must be an integer, got {value!r}")
            if value < 1:
                raise ProfileError(f"{label} must be at least 1, got {value}")
        if len(self.max_controller_size) != 3:
            raise ProfileError("max_controller_size needs exactly 3 values (x, y, z)")
        if not isinstance(self.channels_enabled, bool):
            raise ProfileError("channels_enabled must be true or false")
        if not self.name.strip():
            raise ProfileError("profile name cannot be empty")

    def warnings(self) -> list[str]:
        """Values that are legal here but unusual or beyond known fork limits."""
        out = []
        for label, value in (
            ("Normal cable capacity", self.normal_channel_capacity),
            ("Dense cable capacity", self.dense_channel_capacity),
        ):
            if value > KNOWN_CAPACITY_MAX:
                out.append(f"{label} {value} is above AE2 UEL's limit of {KNOWN_CAPACITY_MAX}.")
        for axis, value in zip("XYZ", self.max_controller_size):
            if value > KNOWN_SIZE_MAX:
                out.append(f"Controller size {axis}={value} is above AE2 UEL's limit of {KNOWN_SIZE_MAX}.")
        if self.normal_channel_capacity > self.dense_channel_capacity:
            out.append("Normal cable capacity is higher than dense; tunnel cells will "
                       "out-carry dense trunks, which is unusual.")
        if not self.channels_enabled:
            out.append("Channels are disabled: every device works without a controller, "
                       "so there is nothing to optimize.")
        return out

    def with_values(self, **changes) -> "Profile":
        return replace(self, **changes)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["max_controller_size"] = list(self.max_controller_size)
        d["schema"] = SCHEMA_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        d = dict(d)
        d.pop("schema", None)
        if "max_controller_size" in d:
            d["max_controller_size"] = tuple(d["max_controller_size"])
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


DEFAULT_PROFILE = Profile()


def default_store_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "ae2opt" / "profiles"


class ProfileStore:
    """Named profiles saved as JSON files. The default profile is read-only."""

    def __init__(self, directory: str | os.PathLike | None = None):
        self.directory = Path(directory) if directory else default_store_dir()

    def _path(self, name: str) -> Path:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "profile"
        return self.directory / f"{slug}.json"

    def list(self) -> list[Profile]:
        profiles = [DEFAULT_PROFILE]
        if self.directory.is_dir():
            for p in sorted(self.directory.glob("*.json")):
                try:
                    profiles.append(Profile.from_dict(json.loads(p.read_text("utf-8"))))
                except (ValueError, TypeError, OSError):
                    continue  # corrupt file: skip rather than break the list
        return profiles

    def get(self, name: str) -> Profile:
        if name == DEFAULT_PROFILE.name:
            return DEFAULT_PROFILE
        path = self._path(name)
        if not path.is_file():
            raise KeyError(name)
        return Profile.from_dict(json.loads(path.read_text("utf-8")))

    def save(self, profile: Profile) -> Path:
        if profile.name == DEFAULT_PROFILE.name:
            raise ProfileError("The default profile is read-only; save under a new name.")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(profile.name)
        path.write_text(json.dumps(profile.to_dict(), indent=2), "utf-8")
        return path

    def delete(self, name: str) -> None:
        if name == DEFAULT_PROFILE.name:
            raise ProfileError("The default profile cannot be deleted.")
        self._path(name).unlink(missing_ok=True)
