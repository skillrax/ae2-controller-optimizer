"""The shipped library of known results: proven optima and best-known builds.

A proof only holds for the rules it was proven under, so each entry records the
capacities, the optimizer options and the model version. `match` returns an
entry only when all of them agree with what the user is about to run.

Boxes are stored with sorted sides (e.g. 3x3x4). AE2's rules don't care about
direction, so a 4x3x3 request uses the same entry with its axes swapped
(`load(..., box=...)` permutes positions and part sides).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .geometry import Direction
from .layout import CableBus, Layout
from .profile import Profile

LIB_DIR = Path(__file__).with_name("library")
INDEX = LIB_DIR / "index.json"


@lru_cache(maxsize=1)
def entries() -> tuple[dict, ...]:
    if not INDEX.is_file():
        return ()
    return tuple(json.loads(INDEX.read_text("utf-8"))["entries"])


def get(entry_id: str) -> dict:
    for e in entries():
        if e["id"] == entry_id:
            return e
    raise KeyError(entry_id)


def rules_match(r: dict, profile: Profile, exterior: bool = True, interior: bool = True,
                anchor_free: bool = True) -> bool:
    """Whether a result recorded under rules `r` (a library entry's or a run's) holds
    for these settings. Runs with custom constraints never match."""
    from .optimize import MODEL_VERSION
    return (r["normal"] == profile.normal_channel_capacity and r["dense"] == profile.dense_channel_capacity
            and r["model"] == MODEL_VERSION and r.get("margin", 1) == 1 and not r.get("custom")
            and r.get("channels", True) and profile.channels_enabled
            and r["exterior"] == exterior and r["interior"] == interior and r["anchor_free"] == anchor_free)


def match(profile: Profile, box, exterior: bool = True, interior: bool = True,
          anchor_free: bool = True) -> dict | None:
    """The known result for exactly these rules, if there is one."""
    want = sorted(int(b) for b in box)
    for e in entries():
        if sorted(e["box"]) == want and rules_match(e["rules"], profile, exterior, interior, anchor_free):
            return e
    return None


def orient(lay: Layout, stored, box) -> Layout:
    """`lay`, built in a box with sides `stored`, turned to fit `box` (same sides, any order)."""
    if list(box) == list(stored):
        return lay
    if sorted(box) != sorted(stored):
        raise ValueError(f"a {list(stored)} build doesn't fit a {list(box)} box")
    return permute_layout(lay, _permutation(list(stored), list(box)))


def _permutation(stored, box) -> tuple[int, int, int]:
    """perm with box[i] == stored[perm[i]]."""
    left = list(range(3))
    perm = []
    for b in box:
        i = next(i for i in left if stored[i] == b)
        left.remove(i)
        perm.append(i)
    return tuple(perm)


def permute_layout(lay: Layout, perm) -> Layout:
    """New axis i is old axis perm[i]: positions and part sides both move."""
    def p(v):
        return tuple(v[perm[i]] for i in range(3))

    side = {d: Direction(p(d.offset)) for d in Direction}
    out = Layout()
    for pos, b in lay.blocks.items():
        if isinstance(b, CableBus):
            b = CableBus(b.cable, b.color, {side[d]: part for d, part in b.parts.items()})
        out.place(p(pos), b)
    return out


def load(entry_id: str, box=None) -> Layout:
    e = get(entry_id)
    lay = Layout.load(LIB_DIR / e["file"])
    if box is not None and list(box) != list(e["box"]):
        if sorted(box) != sorted(e["box"]):
            raise ValueError(f"{entry_id} is a {e['box']} box, not {list(box)}")
        lay = orient(lay, e["box"], box)
    return lay
