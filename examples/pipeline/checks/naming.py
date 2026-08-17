"""Flags identifiers longer than anyone wants to read."""

import re

from ..config import SETTINGS
from ..registry import register

#: Compiled once, on import. Another call with no calling function.
IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


@register("naming")
def check_naming(lines):
    """Every identifier past the configured length, with the name itself."""
    limit = SETTINGS["name_length"]
    found = []
    for number, text in enumerate(lines, 1):
        for name in IDENTIFIER.findall(text):
            if len(name) > limit:
                found.append((number, f"{name} is {len(name)} characters"))
    return found
