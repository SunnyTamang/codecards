"""A small text-checking pipeline, written to exercise import-time flow.

Run it with `python -m pipeline FILE...` from the directory above this one.

Each module here is a different shape of the same problem: work that happens
because a module was imported, rather than because a function was called. See
README.md for what each one is demonstrating.
"""

from .registry import available, register, run

__all__ = ["available", "register", "run"]
