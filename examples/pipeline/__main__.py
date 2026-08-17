"""The way in: read a file, run every registered check over it, print a report."""

import sys

from . import checks  # noqa: F401 - imported for the registrations it performs
from .registry import available, run
from .report import Report


def read_lines(path):
    """The source under inspection, as lines without their endings."""
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


def inspect(path):
    """Run every registered check over one file and collect what they found."""
    lines = read_lines(path)
    report = Report(path)
    for name in available():
        report.add(name, run(name, lines))
    return report


def main(argv):
    """Check every path given, print a report each, and fail if any did."""
    if not argv:
        print("usage: python -m pipeline FILE...", file=sys.stderr)
        return 2
    failed = False
    for path in argv:
        report = inspect(path)
        print(report.format())
        failed = failed or not report.is_clean()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
