"""Tolerant import of an AE2 config file into a Profile.

The importer never applies anything on its own: it returns the profile it
would build plus a per-field status (from file / default / unparsable) so the
UI can show it as a pre-filled manual form for the user to confirm.

Matching is by key name anywhere in the file, not by category path, so forks
that move a setting still import. Supported syntaxes:
  * Forge 1.12 .cfg   (``category { I:key=value }``, quoted keys, ``< >`` lists)
  * plain ``key = value`` / ``key: value`` lines (properties, TOML-ish)
  * JSON (nested objects are flattened)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .profile import DEFAULT_PROFILE, Profile, ProfileError

# Field -> accepted key names (compared case-insensitively, ignoring _ - . and
# spaces). Adding support for another fork means adding names here.
ALIASES: dict[str, tuple[str, ...]] = {
    "normal_channel_capacity": (
        "normalChannelCapacity", "normalChannels", "normalCableChannels",
        "channelsPerCable", "cableChannelCapacity", "smartCableChannels",
    ),
    "dense_channel_capacity": (
        "denseChannelCapacity", "denseChannels", "denseCableChannels",
        "channelsPerDenseCable", "denseCableChannelCapacity",
    ),
    "size_x": ("maxControllerSizeX", "controllerSizeX", "maxControllerX"),
    "size_y": ("maxControllerSizeY", "controllerSizeY", "maxControllerY"),
    "size_z": ("maxControllerSizeZ", "controllerSizeZ", "maxControllerZ"),
    # A single value applied to all three axes, used only where an axis key is missing.
    "size_all": ("maxControllerSize", "controllerSize", "maxControllerDimension"),
    "channels_enabled": ("Channels", "channelsEnabled", "enableChannels", "useChannels"),
}

FIELDS = ("normal_channel_capacity", "dense_channel_capacity",
          "size_x", "size_y", "size_z", "channels_enabled")

FROM_FILE = "from file"
DEFAULT = "default (key not found)"
UNPARSABLE = "couldn't parse"


def _norm(key: str) -> str:
    return re.sub(r"[\s_.\-]", "", key).lower()


_ALIAS_LOOKUP = {_norm(a): f for f, names in ALIASES.items() for a in names}


@dataclass
class Entry:
    key: str
    value: str
    location: str  # "category.sub (line N)"


@dataclass
class FieldStatus:
    status: str
    value: object
    raw: str | None = None
    location: str | None = None


@dataclass
class ImportResult:
    profile: Profile
    fields: dict[str, FieldStatus]
    recognized: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.recognized:
            return ("No AE2 channel or controller-size settings were recognized in this file. "
                    "Using defaults; enter values manually if your fork differs.")
        found = sum(1 for f in self.fields.values() if f.status == FROM_FILE)
        return f"Read {found} of {len(self.fields)} settings from the file; review before applying."


# --------------------------------------------------------------------------- parsing

_LINE = re.compile(
    r"""^\s*(?:[A-Za-z]:)?          # optional Forge type prefix (I:, B:, S:, D:)
        (?:"(?P<qkey>[^"]+)"|(?P<key>[^\s=:"{}<>#]+))
        \s*[=:]\s*(?P<value>.*?)\s*$""",
    re.VERBOSE,
)
_CATEGORY_OPEN = re.compile(r'^\s*(?:"(?P<q>[^"]+)"|(?P<n>[^\s{]+))\s*\{\s*$')


def parse_entries(text: str) -> list[Entry]:
    """Every key/value pair in the text, with its category path."""
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return list(_flatten_json(json.loads(text)))
        except ValueError:
            pass  # not JSON after all; fall through to line parsing

    entries: list[Entry] = []
    stack: list[str] = []
    in_list = False
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        if in_list:
            if line.strip().startswith(">"):
                in_list = False
            continue
        m = _CATEGORY_OPEN.match(line)
        if m:
            stack.append(m.group("q") or m.group("n"))
            continue
        if line.strip() == "}":
            if stack:
                stack.pop()
            continue
        if line.rstrip().endswith("<"):
            in_list = True  # Forge list value; contents are not settings we need
            continue
        m = _LINE.match(line)
        if m:
            key = m.group("qkey") or m.group("key")
            value = m.group("value").strip().strip('"').rstrip(",")
            loc = ".".join(stack) or "(top level)"
            entries.append(Entry(key, value, f"{loc} (line {lineno})"))
    return entries


def _flatten_json(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                yield from _flatten_json(v, path + (str(k),))
            else:
                yield Entry(str(k), json.dumps(v) if not isinstance(v, str) else v,
                            ".".join(path) or "(top level)")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten_json(v, path + (str(i),))


def _parse_int(raw: str) -> int | None:
    try:
        return int(raw.strip())
    except ValueError:
        try:
            f = float(raw.strip())
        except ValueError:
            return None
        return int(f) if f.is_integer() else None


def _parse_bool(raw: str) -> bool | None:
    v = raw.strip().lower()
    if v in ("true", "yes", "on", "1", "enabled"):
        return True
    if v in ("false", "no", "off", "0", "disabled"):
        return False
    return None


# --------------------------------------------------------------------------- import

def import_text(text: str, name: str = "Imported config", base: Profile = DEFAULT_PROFILE) -> ImportResult:
    matches: dict[str, list[Entry]] = {}
    for e in parse_entries(text):
        f = _ALIAS_LOOKUP.get(_norm(e.key))
        if f:
            matches.setdefault(f, []).append(e)

    warnings: list[str] = []
    resolved: dict[str, FieldStatus] = {}
    base_values = {
        "normal_channel_capacity": base.normal_channel_capacity,
        "dense_channel_capacity": base.dense_channel_capacity,
        "size_x": base.max_controller_size[0],
        "size_y": base.max_controller_size[1],
        "size_z": base.max_controller_size[2],
        "size_all": None,
        "channels_enabled": base.channels_enabled,
    }

    for f in (*FIELDS, "size_all"):
        found = matches.get(f, [])
        parser = _parse_bool if f == "channels_enabled" else _parse_int
        if not found:
            resolved[f] = FieldStatus(DEFAULT, base_values[f])
            continue
        parsed = [(e, parser(e.value)) for e in found]
        good = [(e, v) for e, v in parsed if v is not None]
        if not good:
            e = found[0]
            resolved[f] = FieldStatus(UNPARSABLE, base_values[f], e.value, e.location)
            warnings.append(f"{e.key} = {e.value!r} at {e.location} is not a valid value; using the default.")
            continue
        e, v = good[0]
        if f == "channels_enabled" and not isinstance(v, bool):
            v = bool(v)
        if len({val for _, val in good}) > 1:
            where = ", ".join(f"{x.key}={x.value} at {x.location}" for x, _ in good)
            warnings.append(f"Conflicting values for {f}: {where}. Using the first.")
        resolved[f] = FieldStatus(FROM_FILE, v, e.value, e.location)

    # A single "size for all axes" key fills axes that had no key of their own.
    size_all = resolved.pop("size_all")
    if size_all.status == FROM_FILE:
        for axis in ("size_x", "size_y", "size_z"):
            if resolved[axis].status == DEFAULT:
                resolved[axis] = FieldStatus(FROM_FILE, size_all.value, size_all.raw, size_all.location)

    recognized = any(s.status != DEFAULT for s in resolved.values())
    values = {f: s.value for f, s in resolved.items()}
    try:
        profile = Profile(
            name=name,
            normal_channel_capacity=values["normal_channel_capacity"],
            dense_channel_capacity=values["dense_channel_capacity"],
            max_controller_size=(values["size_x"], values["size_y"], values["size_z"]),
            channels_enabled=values["channels_enabled"],
            source="imported config",
        )
    except ProfileError as err:
        # A value parsed but is meaningless (e.g. 0). Fall back field by field.
        warnings.append(f"{err}; affected values reset to defaults.")
        for f, s in resolved.items():
            if f != "channels_enabled" and isinstance(s.value, int) and s.value < 1:
                resolved[f] = FieldStatus(UNPARSABLE, base_values[f], s.raw, s.location)
        values = {f: s.value for f, s in resolved.items()}
        profile = Profile(
            name=name,
            normal_channel_capacity=values["normal_channel_capacity"],
            dense_channel_capacity=values["dense_channel_capacity"],
            max_controller_size=(values["size_x"], values["size_y"], values["size_z"]),
            channels_enabled=values["channels_enabled"],
            source="imported config",
        )
    warnings.extend(profile.warnings())
    return ImportResult(profile, resolved, recognized, warnings)


def import_file(path: str | Path, name: str | None = None, base: Profile = DEFAULT_PROFILE) -> ImportResult:
    p = Path(path)
    text = p.read_text("utf-8", errors="replace")
    return import_text(text, name or f"Imported: {p.name}", base)
