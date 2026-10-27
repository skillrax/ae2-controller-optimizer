"""Command line: python -m ae2opt <command> ...

  check LAYOUT.json [profile options]   simulate a layout and list rule violations
  optimize [--size X Y Z] [--time S] [--out FILE] [profile options]
                                        find the best controller + tunnel layout
  run [--box X Y Z] [--hours H] [--dir DIR] | run --resume DIR
                                        long run: live panel, p/r/q pause/resume/stop, checkpoints
  export LAYOUT.json OUT.nbt [--rig]   structure file for a structure block (optionally with test rig)
  export-tests [--out DIR]              in-game validation suite: .nbt files + TESTING.md checklist
  guide LAYOUT.json [--out FILE.html]   build guide: materials, layers, input list, tunnel network
  show LAYOUT.json                      print a layout layer by layer
  import-cfg CONFIG [--save NAME]       show what a config file imports as
  profiles                              list saved profiles
  gui [--port N] [--no-browser]         open the app (3D designer, optimizer runs, profiles) in your browser

Profile options (later ones override earlier ones):
  --profile NAME      a saved profile
  --cfg PATH          import values from a config file
  --normal N --dense N --size X Y Z --channels on|off   manual values
"""

from __future__ import annotations

import argparse
import sys

from .cfgimport import import_file
from .layout import Layout
from .profile import DEFAULT_PROFILE, Profile, ProfileError, ProfileStore
from .render import render_layers
from .rules import check


def _profile_from_args(args) -> Profile:
    store = ProfileStore()
    profile = store.get(args.profile) if args.profile else DEFAULT_PROFILE
    if args.cfg:
        result = import_file(args.cfg, base=profile)
        print(result.summary, file=sys.stderr)
        for w in result.warnings:
            print(f"  warning: {w}", file=sys.stderr)
        profile = result.profile
    changes = {}
    if args.normal is not None:
        changes["normal_channel_capacity"] = args.normal
    if args.dense is not None:
        changes["dense_channel_capacity"] = args.dense
    if args.size is not None:
        changes["max_controller_size"] = tuple(args.size)
    if args.channels is not None:
        changes["channels_enabled"] = args.channels == "on"
    if changes:
        profile = profile.with_values(name=f"{profile.name} (edited)", source="manual", **changes)
    return profile


def _spec_from_args(args, time_limit: float, lns_time: float, log: bool = False):
    from .optimize import OptimizeSpec
    start = frozenset(Layout.load(args.start).controllers()) if args.start else None
    return OptimizeSpec(size=tuple(args.box) if args.box else None, time_limit=time_limit,
                        workers=args.workers, target_tunnels=args.target,
                        exterior_tunnels=not args.no_exterior, interior_tunnels=not args.no_interior,
                        start=start, seed_patterns=args.seeds, seed_time=args.seed_time,
                        lns_time=lns_time, lns_step=args.lns_step, symmetry_breaking=not args.no_symmetry,
                        random_seed=args.random_seed, log=log)


def _run_command(args) -> int:
    import math
    import time
    from pathlib import Path
    from . import runner
    if args.resume:
        run_dir = Path(args.resume)
        runner.prepare_resume(run_dir)
    else:
        profile = _profile_from_args(args)
        if args.mode == "prove":
            spec = _spec_from_args(args, time_limit=math.inf, lns_time=0.0)
        else:
            spec = _spec_from_args(args, time_limit=args.main, lns_time=math.inf)
        box = args.box or profile.max_controller_size
        run_dir = Path(args.dir or f"runs/{'x'.join(map(str, box))}-{time.strftime('%Y%m%d-%H%M%S')}")
        runner.new_run(run_dir, profile, spec)
    return runner.control(run_dir, args.hours, low_priority=not args.normal_priority)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ae2opt", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def profile_options(p):
        p.add_argument("--profile")
        p.add_argument("--cfg")
        p.add_argument("--normal", type=int)
        p.add_argument("--dense", type=int)
        p.add_argument("--size", type=int, nargs=3, metavar=("X", "Y", "Z"),
                       help="controller size limit (the profile's maxControllerSize)")
        p.add_argument("--channels", choices=("on", "off"))

    c = sub.add_parser("check", help="simulate a layout and report rule violations")
    c.add_argument("layout")
    profile_options(c)

    def search_options(p):
        profile_options(p)
        p.add_argument("--box", type=int, nargs=3, metavar=("X", "Y", "Z"),
                       help="box to fill (default: the controller size limit)")
        p.add_argument("--workers", type=int, default=8)
        p.add_argument("--target", type=int, help="fewest controllers reaching this many tunnels")
        p.add_argument("--no-exterior", action="store_true", help="no tunnel cells outside the box")
        p.add_argument("--no-interior", action="store_true", help="no tunnel cells inside the box")
        p.add_argument("--start", help="warm-start from a saved layout's controllers")
        p.add_argument("--seeds", type=int, default=0, help="lattice patterns to try as warm starts (default off)")
        p.add_argument("--seed-time", type=float, default=10.0, help="seconds per warm-start shape")
        p.add_argument("--lns-step", type=float, default=10.0, help="seconds per neighbourhood")
        p.add_argument("--no-symmetry", action="store_true", help="turn off mirror/axis symmetry breaking")
        p.add_argument("--random-seed", type=int, default=0)

    o = sub.add_parser("optimize", help="find the best controller + tunnel layout")
    search_options(o)
    o.add_argument("--time", type=float, default=60.0, help="seconds for the main solve (default 60)")
    o.add_argument("--lns", type=float, default=0.0, help="seconds of neighbourhood search after the main solve")
    o.add_argument("--out", help="save the layout as JSON")
    o.add_argument("--log", action="store_true", help="show solver progress")

    r = sub.add_parser("run", help="long run with a live progress panel, pause/resume and checkpoints")
    search_options(r)
    r.add_argument("--dir", help="run folder (default: runs/<box>-<date>)")
    r.add_argument("--resume", metavar="DIR", help="continue a stopped run from its folder")
    r.add_argument("--hours", type=float, help="stop and save after this many active hours (pauses don't count)")
    r.add_argument("--mode", choices=("prove", "improve"), default="prove",
                   help="prove: one open-ended solve until proven optimal (small boxes); "
                        "improve: main solve, then neighbourhood search (big boxes)")
    r.add_argument("--main", type=float, default=300.0, help="improve mode: seconds of main solve before LNS")
    r.add_argument("--normal-priority", action="store_true",
                   help="run the solver at normal priority (default: below normal, so the PC stays usable)")

    sub.add_parser("_worker").add_argument("run_dir")  # internal: the solver process of `run`

    e = sub.add_parser("export", help="write a layout as a 1.12.2 structure file (.nbt)")
    profile_options(e)
    e.add_argument("layout")
    e.add_argument("out", help="output .nbt file")
    e.add_argument("--rig", action="store_true",
                   help="wrap the build in an in-game test rig (tunnel network, power, output rigs)")
    e.add_argument("--box", type=int, nargs=3, metavar=("X", "Y", "Z"),
                   help="with --rig: the box the build was optimized in (default: controller bounding box)")

    t = sub.add_parser("export-tests", help="generate the in-game validation suite (.nbt files + checklist)")
    profile_options(t)
    t.add_argument("--out", default="examples/ingame")
    t.add_argument("--time", type=float, default=60.0, help="optimizer seconds per test build")
    t.add_argument("--workers", type=int, default=8)
    t.add_argument("--suite", choices=("main", "colour"), default="main",
                   help="main: T1-T4, N1-N2; colour: colour-separation tests and a coloured 3x3x3")
    t.add_argument("--fresh", action="store_true",
                   help="re-optimize the test builds instead of reusing the ones already in --out")

    gd = sub.add_parser("guide", help="build guide (HTML): materials, layers, input list, tunnel-network sizing")
    profile_options(gd)
    gd.add_argument("layout")
    gd.add_argument("--out", help="output .html (default: next to the layout)")
    gd.add_argument("--title")
    gd.add_argument("--no-tunnel-controller", action="store_true",
                    help="skip designing the tunnel-network controller (no OR-Tools needed)")
    gd.add_argument("--time", type=float, default=60.0, help="seconds for the tunnel-network controller design")
    gd.add_argument("--workers", type=int, default=8)

    s = sub.add_parser("show", help="print a layout layer by layer")
    s.add_argument("layout")

    i = sub.add_parser("import-cfg", help="show what a config file imports as")
    i.add_argument("config")
    i.add_argument("--save", metavar="NAME", help="save the result as a named profile")

    sub.add_parser("profiles", help="list saved profiles")

    gu = sub.add_parser("gui", help="open the app in your browser (local only, works offline)")
    gu.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    gu.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    gu.add_argument("--runs-dir", help="folder for optimizer runs (default: %%APPDATA%%/ae2opt/runs)")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            profile = _profile_from_args(args)
            for w in profile.warnings():
                print(f"profile warning: {w}", file=sys.stderr)
            report = check(Layout.load(args.layout), profile)
            print(f"profile: {profile.name}  normal={profile.normal_channel_capacity} "
                  f"dense={profile.dense_channel_capacity} size={'x'.join(map(str, profile.max_controller_size))}")
            print(report.format())
            return 0 if report.ok else 1
        if args.cmd == "optimize":
            from .optimize import OptimizeSpec, optimize
            profile = _profile_from_args(args)
            spec = _spec_from_args(args, time_limit=args.time, lns_time=args.lns, log=args.log)
            sol = optimize(profile, spec)
            print(f"profile: {profile.name}  normal={profile.normal_channel_capacity} "
                  f"dense={profile.dense_channel_capacity} size={'x'.join(map(str, profile.max_controller_size))}")
            print(sol.summary())
            print(render_layers(sol.layout))
            for v in sol.report.errors():
                print(v)
            if args.out:
                # Saved with tunnel-network ports at the exits (P) so `check` can verify
                # the file on its own; in game, connect your tunnel network there.
                sol.harness.save(args.out)
                print(f"saved to {args.out} (P = where your tunnel network connects)")
            return 0 if sol.report.ok else 1
        if args.cmd == "run":
            return _run_command(args)
        if args.cmd == "_worker":
            from .runner import worker_main
            return worker_main(args.run_dir)
        if args.cmd == "export":
            from .export import save_structure
            lay = Layout.load(args.layout)
            if args.rig:
                from .harness import build_rig
                profile = _profile_from_args(args)
                ctrls = lay.controllers()
                box = tuple(args.box) if args.box else tuple(max(c[i] for c in ctrls) + 1 for i in range(3))
                rig = build_rig("export", lay, box, profile)
                for k, v in rig.expected.items():
                    print(f"  expected {k}: {v}")
                lay = rig.layout
            info = save_structure(lay, args.out)
            print(f"saved {args.out}: {'x'.join(map(str, info['size']))} structure")
            return 0
        if args.cmd == "export-tests":
            from .ingame import write_colour_suite, write_suite
            if args.suite == "colour":
                out = write_colour_suite(args.out, _profile_from_args(args), args.time, args.workers)
            else:
                out = write_suite(args.out, _profile_from_args(args), args.time, args.workers, fresh=args.fresh)
            print(f"wrote the in-game test suite to {out} (start with {out / 'TESTING.md'})")
            return 0
        if args.cmd == "guide":
            from pathlib import Path
            from .guide import build_plan, save_html
            src = Path(args.layout)
            out = Path(args.out) if args.out else src.with_suffix(".html")
            plan = build_plan(Layout.load(src), _profile_from_args(args), title=args.title or f"AE2 build: {src.stem}",
                              design_tunnel_controller=not args.no_tunnel_controller,
                              time_limit=args.time, workers=args.workers)
            save_html(plan, out)
            tn = plan.tunnel_network
            print(f"{len(plan.inputs)} input tunnels, {plan.bom['controllers']} controllers, "
                  f"tunnel network needs {tn['channels']} channels ({tn['outlets']} outlets)")
            for n in plan.notes:
                print(f"note: {n}")
            print(f"saved {out}")
            return 0
        if args.cmd == "show":
            print(render_layers(Layout.load(args.layout)))
            return 0
        if args.cmd == "import-cfg":
            result = import_file(args.config, name=args.save)
            print(result.summary)
            for name, f in result.fields.items():
                where = f"  ({f.location})" if f.location else ""
                print(f"  {name:<24} {f.value!s:<8} {f.status}{where}")
            for w in result.warnings:
                print(f"  warning: {w}")
            if args.save:
                print(f"saved to {ProfileStore().save(result.profile)}")
            return 0
        if args.cmd == "gui":
            from pathlib import Path
            from .gui import serve
            return serve(args.port, open_browser=not args.no_browser,
                         runs_dir=Path(args.runs_dir) if args.runs_dir else None)
        if args.cmd == "profiles":
            for p in ProfileStore().list():
                print(f"{p.name}: normal={p.normal_channel_capacity} dense={p.dense_channel_capacity} "
                      f"size={'x'.join(map(str, p.max_controller_size))} channels={'on' if p.channels_enabled else 'off'}")
            return 0
    except (ProfileError, KeyError, OSError, ValueError, RuntimeError) as err:  # incl. FileExistsError
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
