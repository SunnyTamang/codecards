"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .extract import analyze
from .render.bundle import write_html
from .render.viewmodel import build_viewmodel

#: Above this many callables the expanded view stops being readable.
LARGE_GRAPH_THRESHOLD = 5000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codecards",
        description="Static call-graph cards and flow walkthroughs for Python codebases.",
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH",
                        help="files or directories to analyse")
    parser.add_argument("-o", "--output", type=Path, default=Path("codecards.html"),
                        help="output file (default: codecards.html)")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                        help="glob to skip, repeatable")
    parser.add_argument("--include-external", action="store_true",
                        help="draw stdlib and third-party targets as leaf nodes")
    parser.add_argument("--no-source", action="store_true", dest="no_source",
                        help="omit embedded source; cards stop above the card tier")
    parser.add_argument("--max-depth", type=int, default=15, metavar="N",
                        help="walkthrough depth cap (default: 15)")
    parser.add_argument("--open", action="store_true", dest="open_browser",
                        help="open the generated file in the default browser")
    parser.add_argument("--quiet", action="store_true", help="suppress the summary report")
    parser.add_argument("--no-html", action="store_true",
                        help="analyse only, do not write an HTML file")
    parser.add_argument("--scip", type=Path, metavar="INDEX",
                        help="resolve calls from a SCIP index instead of by "
                             "reading the Python source, using tree-sitter to "
                             "find the call sites")
    parser.add_argument("--version", action="version", version=f"codecards {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # A mistyped path is the commonest way to invoke this wrongly, and
    # "no Python files found in ..." reads as though the directory exists and
    # is empty. Say which thing actually went wrong.
    missing = [p for p in args.paths if not p.exists()]
    if missing:
        print(
            "codecards: path does not exist: "
            + ", ".join(str(p) for p in missing),
            file=sys.stderr,
        )
        return 1

    if args.scip:
        # Imported here so the default path never needs tree-sitter installed.
        from .scip import IndexUnusable  # noqa: PLC0415
        from .scip import analyze as analyze_scip  # noqa: PLC0415
        if not args.scip.exists():
            print(f"codecards: no such index: {args.scip}", file=sys.stderr)
            return 1
        try:
            graph, report = analyze_scip(
                roots=args.paths,
                index_path=args.scip,
                embed_source=not args.no_source,
            )
        except IndexUnusable as exc:
            print(f"codecards: {exc}", file=sys.stderr)
            return 1
        _warn_if_stale(report)
    else:
        graph, report = analyze(
            roots=args.paths,
            excludes=tuple(args.exclude),
            include_external=args.include_external,
            embed_source=not args.no_source,
        )

    if not graph.nodes:
        print(
            "codecards: no Python files found in "
            + ", ".join(str(p) for p in args.paths),
            file=sys.stderr,
        )
        return 1

    if not graph.edges:
        print(
            "codecards: found Python files but no calls between them, so there is"
            " nothing to draw. Check the paths, or widen them.",
            file=sys.stderr,
        )
        return 1

    if report.callable_count > LARGE_GRAPH_THRESHOLD:
        print(
            f"codecards: warning - {report.callable_count:,} callables."
            " The module view stays readable but expanding everything will not."
            " Consider narrowing the scope with --exclude.",
            file=sys.stderr,
        )

    if not args.no_html:
        _write_html(graph, report, args)

    if not args.quiet:
        print(report.format())
        if not args.no_html:
            print(f"wrote {args.output}")

    return 0


#: Enough names to recognise what changed without burying the instruction
#: that follows them.
STALE_FILES_SHOWN = 5


def _warn_if_stale(report) -> None:
    """Say when the graph describes code that has since been edited.

    This warns and continues rather than prompting. Someone reading a graph
    wants the graph; being stopped to answer a question about indexing is not
    what they came for, and a prompt cannot be answered at all when the output
    is being piped. The same fact is recorded in the page, so it survives being
    read by someone who never saw this terminal.
    """
    if not report.stale:
        return
    count = len(report.stale)
    print(
        f"codecards: warning - {count} source file{'' if count == 1 else 's'}"
        " changed after the index was built, so this graph describes older"
        " code:",
        file=sys.stderr,
    )
    for name in report.stale[:STALE_FILES_SHOWN]:
        print(f"    {name}", file=sys.stderr)
    if count > STALE_FILES_SHOWN:
        print(f"    ... and {count - STALE_FILES_SHOWN} more", file=sys.stderr)
    if report.reindex_command:
        print(f"  Rebuild it with:\n    {report.reindex_command}", file=sys.stderr)


def _write_html(graph, report, args) -> None:
    viewmodel = build_viewmodel(graph, report, max_depth=args.max_depth)
    write_html(viewmodel, args.output)
    if args.open_browser:
        import webbrowser  # noqa: PLC0415 - only needed for --open

        webbrowser.open(args.output.resolve().as_uri())


def run() -> None:
    sys.exit(main())
