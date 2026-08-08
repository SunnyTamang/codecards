"""Find the .py files to analyse.

Gitignore semantics are delegated to git when the root is inside a work tree.
Everything else falls back to a filesystem walk that skips known noise and
guards against symlink loops via resolved real paths.
"""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXCLUDE_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "env", "node_modules",
    "build", "dist", ".eggs",
})


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: str


def _matches(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return any(
        fnmatch.fnmatch(rel, pattern)
        for pattern in patterns
    )


def _git_listed(root: Path) -> list[Path] | None:
    """Files git considers part of the project, or None if root is not a repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others",
             "--exclude-standard", "-z", "--", "*.py"],
            capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    entries = result.stdout.decode("utf-8", "replace").split("\0")
    return [root / e for e in entries if e]


def _walked(root: Path) -> list[Path]:
    found: list[Path] = []
    seen_dirs: set[Path] = set()
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            real = directory.resolve()
        except OSError:
            continue
        if real in seen_dirs:
            continue
        seen_dirs.add(real)
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in DEFAULT_EXCLUDE_DIRS:
                    stack.append(entry)
            elif entry.suffix == ".py":
                found.append(entry)
    return found


def find_python_files(
    roots: list[Path], excludes: tuple[str, ...] | list[str] = ()
) -> tuple[list[Path], list[SkippedFile]]:
    patterns = tuple(excludes)
    skipped: list[SkippedFile] = []
    collected: set[Path] = set()

    for raw_root in roots:
        root = Path(raw_root)
        if not root.exists():
            skipped.append(SkippedFile(path=str(root), reason="path does not exist"))
            continue
        if root.is_file():
            if root.suffix == ".py":
                collected.add(root.resolve())
            else:
                skipped.append(SkippedFile(path=str(root), reason="not a Python file"))
            continue

        candidates = _git_listed(root)
        if candidates is None:
            candidates = _walked(root)
        else:
            candidates = [
                c for c in candidates
                if not any(part in DEFAULT_EXCLUDE_DIRS for part in c.relative_to(root).parts)
            ]

        for candidate in candidates:
            if not candidate.is_file():
                continue
            if patterns and _matches(candidate, root, patterns):
                continue
            collected.add(candidate.resolve())

    return sorted(collected), skipped
