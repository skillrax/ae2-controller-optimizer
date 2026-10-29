"""Research workspace: exact optima for the big boxes (docs/RESEARCH.md).

Not part of the app. It imports the simulator, layout, library and bounds from `ae2opt`,
so those never drift apart, but runs the solvers bare: no wiring polish, no LNS, no
minimum run time. Only the tunnel count and its proven bound matter here.

    python -m lab solve 3 3 4 --model both --time 600
    python -m lab log
"""
