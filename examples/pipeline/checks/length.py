"""Flags lines that run past the configured limit."""

from ..config import SETTINGS
from ..registry import register


@register("length")
def check_length(lines):
    """Every line longer than the configured width, with its length."""
    limit = SETTINGS["line_length"]
    return [(number, f"line is {len(text)} characters")
            for number, text in enumerate(lines, 1)
            if len(text) > limit]
