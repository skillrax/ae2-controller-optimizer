"""Entry point of the packaged app. With no arguments it opens the app; the same
executable is started with `_worker <run dir>` as the optimizer's solver process."""

import multiprocessing
import sys

from ae2opt.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
