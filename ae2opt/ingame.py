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


def positive_tests(profile: Profile, time_limit: float, workers: int) -> list[tuple[str, str, TestRig]]:
    ring = frozenset(c for c in itertools.product(range(3), [0], range(3)) if c != (1, 0, 1))
    specs = [
        ("T1", "2x2x2 optimum", (2, 2, 2), {}),
        ("T2", "3x1x3 ring around one gap cell", (3, 1, 3), dict(required=ring, forbidden=frozenset({(1, 0, 1)}))),
        ("T3", "3x3x3 optimum", (3, 3, 3), {}),
        ("T4", "4x4x3 optimum", (4, 4, 3), {}),
    ]
    out = []
    for tid, title, size, extra in specs:
        sol = _optimize(size, profile, time_limit, workers, **extra)
        rig = build_rig(tid, sol.harness, size, profile)
        if not rig.expected["valid"]:
            raise RuntimeError(f"{tid} test rig fails its own simulation: {rig.expected['errors']}")
        out.append((tid, title, rig))
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
                workers: int = 8) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []

    positives = positive_tests(profile, time_limit, workers)
    for tid, title, rig in positives:
        name = f"ae2opt_{tid.lower()}"
        info = save_structure(rig.layout, out / f"{name}.nbt")
        rig.layout.save(out / f"{name}.json")
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
             f"{'x'.join(map(str, profile.max_controller_size))}). Your AE2 config must match.\n")
    L.append("**Goal:** check that the game does exactly what the simulator predicts. A single "
             "mismatch means the model is wrong somewhere, and optimizer results can't be trusted "
             "until it's fixed. Note *what* differs: that tells us which rule is off.\n")
    L.append("## Setup (once)\n")
    L.append("1. Copy every `ae2opt_*.nbt` from this folder into your world's `structures` folder "
             "(`saves/<world>/structures/`; create the folder if it doesn't exist).")
    L.append("2. Use a creative world with plenty of open space (a superflat world is easiest).")
    L.append("3. Get a structure block (`/give @p minecraft:structure_block`) and set it to **Load** mode.")
    L.append("4. For each test, type its structure name (for example `ae2opt_t1`). Click **Load** "
             "once to show the bounding box, then again to place it. Keep tests at least "
             "5 blocks apart.")
    L.append("5. Wait about 5 seconds after placing so both networks finish booting.\n")
    L.append("**What's in each positive test:**")
    L.append("- **The build:** main controllers, plus cable cells with input tunnels and cable anchors.")
    L.append("- **A shell:** dense fluix cable two blocks out, which is the tunnel network's cabling.")
    L.append("- **A snake of tunnel-network controllers** under the shell, with a creative energy "
             "cell under it.")
    L.append("- **A creative energy cell on a main controller** (main-network power).")
    L.append("- **Two output rigs on top:** an output tunnel feeding a dense cable with 40 ME "
             "terminals.\n")
    L.append("**How to read the results:**")
    L.append("- **Controller texture:** online (normal), offline (dark), or conflicted (red).")
    L.append("- **P2P tunnel texture:** an active ME P2P tunnel shows its lit face. Inactive ones "
             "are dark.")
    L.append("- **ME terminal screens:** lit means the terminal has a channel. Dark means no channel.")
    L.append("- **Network Tool:** right-click any cable to see that network's channels in use.\n")
    L.append("**The shell's channel count is the main pass/fail check.** Tunnels in gap cells "
             "inside a build are hidden, so you can't count them by eye. Every active tunnel "
             "uses one tunnel-network channel, so the Network Tool on the shell shows the "
             "expected number only if every tunnel is active. Counting lit tunnels on the "
             "outside is a cross-check.\n")
    L.append("Coordinates in the notes are layout coordinates (the `.json` files). In the "
             "placed structure, the corner nearest the structure block is the layout's minimum "
             "corner.\n")

    for r in rows:
        e = r["expected"]
        L.append(f"## {r['id']}: {r['title']}  (`{r['name']}`, {r['size'][0]}x{r['size'][1]}x{r['size'][2]})\n")
        if r["kind"] == "positive":
            L.append("| Check | Expected | In game |")
            L.append("|---|---|---|")
            L.append(f"| Main controllers ({e['main_controllers']} blocks): state | online (not red) | |")
            L.append(f"| Tunnel-network controllers ({e['tunnel_controllers']} blocks, under the shell): state "
                     f"| online | |")
            L.append(f"| Input tunnels lit | **{e['input_tunnels_active']} of {e['input_tunnels']}** | |")
            L.append(f"| Output tunnels lit (top of the shell) | **{e['output_tunnels_active']} of 2** | |")
            L.append(f"| Network Tool on the shell: channels used | **{e['tunnel_network_channels']}** "
                     f"(inputs + outputs) | |")
            L.append(f"| Network Tool on a rig's dense cable: channels used | **{e['main_network_channels']}** | |")
            for i, rig in enumerate(e["rigs"], 1):
                L.append(f"| Rig {i} (frequency {rig['freq']}): lit terminals | **{rig['active']} of "
                         f"{rig['devices']}** | |")
            L.append("")
            notes = []
            if r.get("removed_tunnel"):
                notes.append(f"the main-network power cell sits where one outer tunnel would be "
                             f"({_fmt(r['removed_tunnel'][0])}), so this build has one tunnel fewer "
                             f"than the optimizer's layout")
            if e["busiest_tunnel_cable"] >= 8:
                notes.append(f"at least one tunnel cell's cable carries exactly "
                             f"{e['busiest_tunnel_cable']} tunnels, the normal-cable limit. "
                             "If even one input stays dark, the real limit is lower than 8")
            for n in notes:
                L.append(f"- Note: {n}.")
            if notes:
                L.append("")
        else:
            L.append("| Check | Expected | In game |")
            L.append("|---|---|---|")
            if r["id"] == "N1":
                L.append(f"| Input tunnels lit | **{e['input_tunnels_active']} of {e['input_tunnels']}** "
                         "(the normal cable at x=1 is full) | |")
                L.append("| Controller | online | |")
                L.append("")
                L.append("- The game decides which tunnel stays dark (its pathing order), so it may "
                         "differ from the simulator. Only the **count** matters.\n")
            else:
                L.append("| Main controllers and tunnel-network controllers | **conflicted** (red): both "
                         "networks merged through the uncapped side | |")
                L.append(f"| Input tunnels lit | **{e['input_tunnels_active']} of {e['input_tunnels']}** | |")
                L.append("| Rig terminals lit | **0** | |")
                L.append("")
        L.append("")
    L.append("## Reporting back\n")
    L.append("For each test, fill in the **In game** column, or just list the rows that differ. "
             "Screenshots of anything unexpected help. The `.json` file next to each `.nbt` is "
             "the exact layout; `python -m ae2opt show ae2opt_t1.json` prints it layer by layer.")
    return "\n".join(L) + "\n"
