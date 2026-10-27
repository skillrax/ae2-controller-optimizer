"""Generate the in-game test suite: structure files, layouts and a checklist.

    python -m ae2opt export-tests --out examples/ingame

Positive tests (should work exactly as predicted):
  T1  2x2x2 optimum          -- simplest; tunnels on gap cells inside the box
  T2  3x1x3 ring             -- one gap cell with 4 tunnels, exits up/down only
  T3  3x3x3 optimum          -- interior gaps, cable-to-cable anchors, a tunnel
                                cable loaded to exactly 8
  T4  4x4x3 optimum          -- larger, more corridors
Negative tests (should fail in a specific predicted way):
  N1  capacity overload      -- 9 tunnels routed through one normal cable: 8 light, 1 stays dark
  N2  uncapped face          -- T1 with one tunnel removed so its cable touches a
                                main controller: networks merge -> controller conflict

If any result differs from the prediction, the simulator's model of AE2 is wrong
somewhere and optimizer results cannot be trusted until it is fixed.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from .export import save_structure
from .geometry import Direction, step
from .harness import TestRig, _layer, build_rig, standalone_rig
from .layout import CableBus, CableType, Layout, P2PTunnel
from .profile import DEFAULT_PROFILE, Profile

D = Direction


def _optimize(size, profile, time_limit, workers, **extra):
    from .optimize import OptimizeSpec, optimize
    sol = optimize(profile, OptimizeSpec(size=size, time_limit=time_limit, workers=workers, **extra))
    if not sol.report.ok:
        raise RuntimeError(f"optimizer produced an invalid {size} layout")
    return sol


def _existing_build(out: Path, tid: str, size) -> Layout | None:
    """The build from an earlier suite in `out`, so regenerating keeps the same
    builds a tester may already have placed: `<name>_build.json`, or else the
    build cells (box + 1-block margin) of an older full rig `<name>.json`."""
    name = f"ae2opt_{tid.lower()}"
    if (out / f"{name}_build.json").exists():
        return Layout.load(out / f"{name}_build.json")
    if (out / f"{name}.json").exists():
        old = Layout.load(out / f"{name}.json")
        build = Layout()
        build.blocks = {p: b for p, b in old.blocks.items()
                        if _layer(p, size) <= 1 and b.kind != "energy_acceptor"}
        return build
    return None


def positive_tests(profile: Profile, time_limit: float, workers: int,
                   reuse_from: Path | None = None) -> list[tuple[str, str, TestRig, Layout]]:
    ring = frozenset(c for c in itertools.product(range(3), [0], range(3)) if c != (1, 0, 1))
    specs = [
        ("T1", "2x2x2 optimum", (2, 2, 2), {}),
        ("T2", "3x1x3 ring around one gap cell", (3, 1, 3), dict(required=ring, forbidden=frozenset({(1, 0, 1)}))),
        ("T3", "3x3x3 optimum", (3, 3, 3), {}),
        ("T4", "4x4x3 optimum", (4, 4, 3), {}),
    ]
    out = []
    for tid, title, size, extra in specs:
        build = _existing_build(reuse_from, tid, size) if reuse_from else None
        if build is None:
            build = _optimize(size, profile, time_limit, workers, **extra).harness
        rig = build_rig(tid, build, size, profile)
        if not rig.expected["valid"]:
            raise RuntimeError(f"{tid} test rig fails its own simulation: {rig.expected['errors']}")
        out.append((tid, title, rig, build))
    return out


def capacity_overload() -> Layout:
    """N1: a powered tunnel-network controller, one normal cable, then a dense
    cable feeding two buses with 5 + 4 input tunnels (facing air). Nine tunnels,
    but the normal cable carries at most 8 channels."""
    lay = Layout()
    lay.controller((0, 0, 0))
    lay.energy((0, -1, 0))
    lay.cable((1, 0, 0), CableType.NORMAL)
    lay.cable((2, 0, 0), CableType.DENSE)
    north = [d for d in D if d is not D.SOUTH]
    south = [d for d in D if d is not D.NORTH][:4]
    lay.cable((2, 0, -1), CableType.NORMAL, parts={d: P2PTunnel(i + 1) for i, d in enumerate(north)})
    lay.cable((2, 0, 1), CableType.NORMAL, parts={d: P2PTunnel(i + 6) for i, d in enumerate(south)})
    return lay


def uncapped_face(t1: TestRig) -> Layout:
    """N2: T1's rig with one outer tunnel (not one feeding an output rig) removed,
    leaving that cable cell's side open toward a main controller."""
    lay = Layout()
    lay.blocks = dict(t1.layout.blocks)
    rig_inputs = {o.input_at for o in t1.outputs}
    for pos in lay.positions():
        b = lay.get(pos)
        if not (isinstance(b, CableBus) and _layer(pos, t1.size) == 1):
            continue
        for d, p in b.parts.items():
            if isinstance(p, P2PTunnel) and not p.output and (pos, d) not in rig_inputs \
                    and lay.is_controller(step(pos, d)):
                parts = dict(b.parts)
                del parts[d]
                lay.cable(pos, b.cable, b.color, parts)
                return lay
    raise RuntimeError("T1 has no outer tunnel to remove")


def write_suite(out_dir: str | Path, profile: Profile = DEFAULT_PROFILE, time_limit: float = 60,
                workers: int = 8, fresh: bool = False) -> Path:
    """Write the suite. Builds already in `out_dir` are reused unless `fresh`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []

    positives = positive_tests(profile, time_limit, workers, reuse_from=None if fresh else out)
    for tid, title, rig, build in positives:
        name = f"ae2opt_{tid.lower()}"
        info = save_structure(rig.layout, out / f"{name}.nbt")
        rig.layout.save(out / f"{name}.json")
        build.save(out / f"{name}_build.json")
        rows.append(dict(id=tid, name=name, title=title, kind="positive", size=info["size"],
                         origin=info["origin"], expected=rig.expected, main_power=rig.main_power,
                         tunnel_power=rig.tunnel_power, removed_tunnel=rig.removed_tunnel))

    n1 = capacity_overload()
    exp = standalone_rig("N1", n1, profile)
    info = save_structure(n1, out / "ae2opt_n1.nbt")
    n1.save(out / "ae2opt_n1.json")
    rows.append(dict(id="N1", name="ae2opt_n1", title="capacity overload: 9 tunnels through one normal cable",
                     kind="negative", size=info["size"], origin=info["origin"],
                     expected={k: v for k, v in exp.items() if k != "report"}))

    n2 = uncapped_face(positives[0][2])
    exp = standalone_rig("N2", n2, profile)
    info = save_structure(n2, out / "ae2opt_n2.nbt")
    n2.save(out / "ae2opt_n2.json")
    rows.append(dict(id="N2", name="ae2opt_n2", title="T1 with one tunnel removed (uncapped controller face)",
                     kind="negative", size=info["size"], origin=info["origin"],
                     expected={k: v for k, v in exp.items() if k != "report"}))

    (out / "expected.json").write_text(json.dumps(rows, indent=1, default=str), "utf-8")
    (out / "TESTING.md").write_text(checklist(rows, profile), "utf-8")
    return out


def _fmt(p) -> str:
    return "(" + ", ".join(str(c) for c in p) + ")"


def checklist(rows: list[dict], profile: Profile) -> str:
    L = []
    L.append("# In-game validation checklist\n")
    L.append("Generated by `python -m ae2opt export-tests`. Profile: "
             f"{profile.name} (normal {profile.normal_channel_capacity}, dense "
             f"{profile.dense_channel_capacity}, controller size "
             f"{'x'.join(map(str, profile.max_controller_size))}). Your AE2 config must match. "
             "Works with plain AE2 rv6 and with Extended Life.\n")
    L.append("**Goal:** check that the game shows exactly what the simulator predicts. A single "
             "mismatch means the model is wrong somewhere, and optimizer results can't be trusted "
             "until it's fixed. Note *what* differs: that tells us which rule is off.\n")
    L.append("## You need\n")
    L.append("- **WAILA/HWYLA or The One Probe.** AE2 adds channel info to both:")
    L.append("  - a smart cable shows `X of 8 Channels`; a dense smart cable shows `X of 32 Channels`. "
             "The number is that cable's busiest side.")
    L.append("  - a P2P tunnel or ME terminal shows `Device Online` (powered, has a channel) or "
             "`Device Missing Channel`.")
    L.append("- **F3**, to read the coordinates of the block you're looking at (`Looking at: x y z`).\n")
    L.append("## Setup (once per test)\n")
    L.append("1. Copy every `ae2opt_*.nbt` from this folder into `saves/<world>/structures/` "
             "(create the folder if needed).")
    L.append("2. In a creative superflat world, place a structure block (`/give @p "
             "minecraft:structure_block`), set it to **Load**, and enter the name (for example "
             "`ae2opt_t1`). Leave the offset at 0 1 0. Click **Load** twice.")
    L.append("3. Write down the structure block's position **S**. A block listed below at "
             "`rel (a, b, c)` is at world position **S + (a, 1 + b, c)**.")
    L.append("4. Wait about 5 seconds for both networks to boot.\n")
    L.append("## What's in a positive test\n")
    L.append("- **The build:** main controllers, plus cable cells with input tunnels and cable anchors.")
    L.append("- **Collector:** a few separate trees of dense smart cable, two or three blocks out from "
             "the build, leading down from the build's exits. Each tree ends in one **collector "
             "root** cable sitting directly on a controller below.")
    L.append("- **Tunnel-network controller:** a snake of controllers under everything, with a "
             "creative energy cell under its corner.")
    L.append("- **Main power:** one creative energy cell touching a main controller.")
    L.append("- **Two output rigs on top:** an output tunnel, then a vertical dense smart cable with "
             "40 ME terminals around it.\n")
    L.append("**The main pass/fail check is the collector roots.** Every active tunnel uses one "
             "tunnel-network channel. Each tree carries all of its tunnels down to its root, so "
             "the roots' readings must add up to the tunnel count. Tunnels hidden inside a build "
             "are counted too.\n")

    for r in rows:
        e = r["expected"]
        L.append(f"## {r['id']}: {r['title']}  (`{r['name']}`, {r['size'][0]}x{r['size'][1]}x{r['size'][2]})\n")
        if r["kind"] == "positive":
            L.append("| # | Check | Expected | In game |")
            L.append("|---|---|---|---|")
            L.append(f"| 1 | Main controllers ({e['main_controllers']} blocks) and the snake "
                     f"({e['tunnel_controllers']} blocks): state | all online, none red | |")
            for i, root in enumerate(e["probe_roots"], 1):
                L.append(f"| 2.{i} | Collector root at rel {_fmt(root['rel'])}: WAILA | "
                         f"`{root['channels']} of 32 Channels` | |")
            L.append(f"| 2 | **Sum of the collector roots** | **{e['probe_roots_total']}** "
                     f"({e['input_tunnels']} inputs + {len(e['rigs'])} outputs) | |")
            for i, rig in enumerate(e["rigs"], 1):
                L.append(f"| {2 + i}a | Rig {i}: output tunnel's cable at rel {_fmt(rig['output_cable'])} | "
                         f"`{rig['output_cable_channels']} of 8 Channels` | |")
                L.append(f"| {2 + i}b | Rig {i}: dense cable just above it, rel {_fmt(rig['trunk'])} | "
                         f"`{rig['trunk_channels']} of 32 Channels` | |")
                L.append(f"| {2 + i}c | Rig {i}: terminals showing `Device Online` (frequency {rig['freq']}) | "
                         f"{rig['active']} of {rig['devices']} (spot-check a few) | |")
            n = 3 + len(e["rigs"])
            L.append(f"| {n} | Spot-check input tunnels you can see: WAILA | all `Device Online`; "
                     "any `Device Missing Channel` is a fail | |")
            L.append("")
            notes = []
            if r.get("removed_tunnel"):
                notes.append("the main-network power cell sits where one outer tunnel would be, so this "
                             "build has one tunnel fewer than the optimizer's layout")
            if e["busiest_tunnel_cable"] >= 8:
                notes.append(f"at least one tunnel cell's cable carries exactly {e['busiest_tunnel_cable']} "
                             "tunnels, the normal-cable limit. If the sum comes up short, the real "
                             "limit may be lower than 8")
            full = [root for root in e["probe_roots"] if root["channels"] >= 32]
            if full:
                notes.append(f"{len(full)} collector root(s) are completely full (32 of 32)")
            for note in notes:
                L.append(f"- Note: {note}.")
            L.append("- Every exit cable's predicted reading is in `expected.json` (`probe_exits`), "
                     "if a sum is off and you want to find where.")
            L.append("")
        else:
            L.append("| # | Check | Expected | In game |")
            L.append("|---|---|---|---|")
            if r["id"] == "N1":
                L.append("| 1 | Controller | online | |")
                L.append("| 2 | Smart cable next to the controller, rel (1, 1, 1): WAILA | `8 of 8 Channels` | |")
                L.append(f"| 3 | The {e['input_tunnels']} P2P tunnels on the two cable buses: WAILA | "
                         f"**{e['input_tunnels_active']}** `Device Online`, "
                         f"**{e['input_tunnels'] - e['input_tunnels_active']}** `Device Missing Channel` | |")
                L.append("")
                L.append("- Which tunnel goes without depends on the game's pathing order; only the "
                         "**count** matters.\n")
            else:
                L.append("| 1 | Main controllers and snake controllers | **conflicted** (red): the networks "
                         "merged through the uncapped side | |")
                L.append("| 2 | Any input tunnel: WAILA | `Device Missing Channel` | |")
                L.append(f"| 3 | Collector roots: WAILA | `0 of 32 Channels` (sum **0** of "
                         f"{e['input_tunnels']} tunnels) | |")
                L.append("| 4 | Rig terminals | none `Device Online` | |")
                L.append("")
        L.append("")
    L.append("## Reporting back\n")
    L.append("Fill in the **In game** column, or just list the rows that differ. Screenshots of "
             "anything unexpected help. The `_build.json` file next to each test is the build "
             "alone, and the `.json` is the full rig. `python -m ae2opt show <file>.json` prints "
             "either layer by layer.")
    return "\n".join(L) + "\n"
