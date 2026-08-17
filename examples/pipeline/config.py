"""Settings, assembled at import time rather than by anyone calling for them."""

import os

DEFAULT_LIMITS = {"line_length": 88, "name_length": 30}


def _from_environment(defaults):
    """Overlay anything set in the environment onto the defaults."""
    resolved = dict(defaults)
    for key in defaults:
        override = os.environ.get("PIPELINE_" + key.upper())
        if override is not None:
            resolved[key] = int(override)
    return resolved


#: No function in this program calls `_from_environment`. It runs once, on the
#: import of this module, and every check reads the result. Built from function
#: bodies alone this module has one definition, no callers, and no outgoing
#: calls - it reads as inert data.
SETTINGS = _from_environment(DEFAULT_LIMITS)
