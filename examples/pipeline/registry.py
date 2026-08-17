"""The table every check writes itself into, and the dispatch that reads it."""

CHECKS = {}


def register(name):
    """Bind a function into the registry under `name`.

    Used as a decorator, so the call happens while the defining module is
    being imported - not while any function is running.
    """
    def bind(function):
        CHECKS[name] = function
        return function
    return bind


def available():
    """Every check that has registered itself so far."""
    return sorted(CHECKS)


def run(name, lines):
    """Dispatch through the registry.

    This call is genuinely unresolvable and should stay that way: the target
    depends on which modules were imported, which is a runtime fact. It is
    here so the fixture shows the honest limit next to the cases that are
    only missing because nothing models import-time flow.
    """
    return CHECKS[name](lines)
