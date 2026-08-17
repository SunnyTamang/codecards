"""Importing this package is what puts the checks into the registry.

Neither name below is referenced anywhere else in the program. The imports
exist entirely for their side effect, and deleting either one silently removes
a check from every run. This is the load-bearing line of the whole example, and
a call graph built from function bodies alone cannot see it at all.
"""

from . import length, naming  # noqa: F401
