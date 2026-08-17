"""Turning what the checks found into something worth printing."""


class Report:
    """Findings for one file, grouped by the check that produced them."""

    def __init__(self, source):
        self.source = source
        self.findings = {}

    def add(self, check, findings):
        """Record what one check found, dropping it if it found nothing."""
        if findings:
            self.findings[check] = findings

    def is_clean(self):
        return not self.findings

    def format(self):
        """One line per finding, or a single line saying there were none."""
        if self.is_clean():
            return f"{self.source}: clean"
        lines = [f"{self.source}:"]
        for check in sorted(self.findings):
            for number, message in self.findings[check]:
                lines.append(f"  {number}: {message} ({check})")
        return "\n".join(lines)
