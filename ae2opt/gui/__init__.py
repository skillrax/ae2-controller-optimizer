"""Local desktop GUI: a small HTTP server on 127.0.0.1 plus a browser page.

    python -m ae2opt gui

Everything runs offline: the server is the standard library, and the page
(static/) has no external scripts, fonts or stylesheets.
"""

from .server import serve

__all__ = ["serve"]
