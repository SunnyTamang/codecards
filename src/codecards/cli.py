"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .render.bundle import write_html
from .render.viewmodel import build_viewmodel

#: Above this many callables the expanded view stops being readable.
LARGE_GRAPH_THRESHOLD = 5000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codecards",
        description="Static call-graph cards and flow walkthroughs. Calls are "
                    "resolved by a language server when one is installed, and "
                    "matched by name when not.",
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
                        help="resolve calls from a SCIP index you built "
                             "beforehand. Slower to set up than a language "
                             "server and portable in a way a server is not: an "
                             "index is a file, so the same graph builds in CI "
                             "and on anyone else's machine")
    parser.add_argument("--lsp", action="store_true",
                        help="require a language server: fail rather than fall "
                             "back to matching calls by name. Use this in CI, "
                             "where a quietly degraded graph is worse than a "
                             "failed build")
    parser.add_argument("--no-lsp", action="store_true", dest="no_lsp",
                        help="never start a language server, even if one is "
                             "installed")
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
            _suggest_an_index(args.paths, output=args.scip)
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
        resolved = _read_with_a_server(args)
        if resolved is not None:
            graph, report = resolved
        else:
            # No server, so names are all there is to go on. Nothing here is
            # certain, so every edge is marked as the guess it is - still a
            # great deal more use than telling someone their project is empty.
            structural = _read_structure(args)
            if structural is None:
                return _nothing_to_read(args)
            graph, report = structural

    if not graph.nodes:
        return _nothing_to_read(args)

    if not graph.edges:
        print(
            "codecards: read the source but found no calls between anything in"
            " it, so there is nothing to draw. Check the paths, or widen them.",
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


def _read_with_a_server(args):
    """Resolve by asking a language server, when one is installed.

    Returns None to mean "fall back", which is not the same as failing: a
    machine without a server should still get a graph. `--lsp` turns that
    silence into an error, which is what a CI run wants; `--no-lsp` skips the
    attempt entirely.

    Which server is used is printed. codecards starts a subprocess here, and
    a tool that quietly runs something on your machine is a tool you cannot
    reason about.
    """
    if args.no_lsp:
        return None
    from . import lsp  # noqa: PLC0415 - only when a graph is actually built

    server = lsp.server_for(args.paths)
    if server is None:
        if args.lsp:
            print("codecards: --lsp was asked for and no language server is "
                  "installed for this project.", file=sys.stderr)
            _suggest_a_server(args.paths)
            raise SystemExit(1)
        return None

    print(f"codecards: resolving with {server[0]}", file=sys.stderr)
    try:
        return lsp.analyze(
            roots=args.paths,
            server=server,
            excludes=tuple(args.exclude),
            include_external=args.include_external,
            embed_source=not args.no_source,
        )
    except lsp.ServerUnusable as exc:
        print(f"codecards: {exc}", file=sys.stderr)
        if args.lsp:
            raise SystemExit(1) from None
        print("codecards: falling back to matching calls by name.",
              file=sys.stderr)
        return None


def _suggest_a_server(paths) -> None:
    """Name the server this project would need, and what installs it."""
    from .parse import grammars  # noqa: PLC0415

    for path in paths:
        if not path.is_dir():
            continue
        for grammar, count in grammars.for_tree(path):
            if not grammar.lsp_command:
                continue
            print(f"\n  {count} {grammar.name.title()} file"
                  f"{'' if count == 1 else 's'} are here, resolved by "
                  f"{grammar.lsp_command[0]}:\n", file=sys.stderr)
            if grammar.lsp_install:
                print(f"    {grammar.lsp_install}\n", file=sys.stderr)
            return


def _nothing_to_read(args) -> int:
    """Nothing any grammar recognises. Say which languages are read, since
    "found nothing" reads as an empty directory rather than as a language
    this tool has no parser for."""
    where = ", ".join(str(p) for p in args.paths)
    from .parse import grammars  # noqa: PLC0415

    known = ", ".join(sorted(g.name for g in grammars.ALL))
    print(f"codecards: found no source it can read in {where}."
          f" It reads: {known}.", file=sys.stderr)
    return 1


def _read_structure(args):
    """Fall back to names alone, when nothing better is available.

    Returns None when there is nothing any grammar recognises, leaving the
    caller to say so. Every edge this draws is a guess and is marked as one,
    which is still a great deal more use than refusing to draw anything.
    """
    from .parse import structural  # noqa: PLC0415

    excludes = tuple(args.exclude)
    if not structural.source_files([Path(p) for p in args.paths], excludes):
        return None
    graph, report = structural.analyze(
        roots=args.paths, excludes=excludes,
        embed_source=not args.no_source)
    if not graph.nodes:
        return None
    # A server is the cheaper of the two upgrades - nothing to build and
    # nothing to keep fresh - so it is offered first.
    print(
        "codecards: no language server, so calls are matched by name and every"
        " edge is marked inferred or ambiguous. For a resolved graph:",
        file=sys.stderr,
    )
    _suggest_a_server(args.paths)
    _suggest_an_index(args.paths)
    return graph, report


def _suggest_an_index(paths, output: Path | None = None) -> None:
    """Say what is actually in the directory, and how to make it readable.

    "No Python files found" is true of a Go project and useless to the person
    holding one: it reads as an empty directory rather than as a language this
    tool reaches through an index. The two commands below are the entire
    distance between a dead end and a graph, and nothing else in the program
    was telling anyone about them.
    """
    from .parse import grammars  # noqa: PLC0415 - the default path never needs this

    found: list[tuple] = []
    for path in paths:
        if path.is_dir():
            found = grammars.for_tree(path)
            if found:
                break
    for grammar, count in found:
        if grammar.indexer_command is None or grammar.name == "python":
            continue
        # Resolved rather than as typed. An indexer interprets these paths
        # itself, and scip-python gets both a relative one and a symlinked
        # one wrong the same silent way: a valid index holding nothing, exit
        # 0. Handing someone the form that fails is worse than saying nothing.
        target = (output or Path("index.scip")).resolve()
        where = Path(paths[0]).resolve()
        print(
            f"\n  {count} {grammar.name.title()} file{'' if count == 1 else 's'} are"
            f" here. {grammar.name.title()} is read through a SCIP index:\n",
            file=sys.stderr,
        )
        if grammar.indexer_install:
            print(f"    {grammar.indexer_install}", file=sys.stderr)
        print(
            "    "
            + grammar.indexer_command.format(root=where, output=target)
            + f"\n\n  then: codecards {paths[0]} --scip {target}",
            file=sys.stderr,
        )
        return


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
        # An indexer resolves imports through the interpreter it can see. Run
        # outside the project's environment it still exits 0, having resolved
        # far less: on a 46-file project, 77 fewer edges and no error saying
        # why. Naming the condition costs one line and is the difference
        # between the command working and appearing to.
        print(
            "  Rebuild it with, from the environment the project runs in:\n"
            f"    {report.reindex_command}",
            file=sys.stderr,
        )


def _write_html(graph, report, args) -> None:
    viewmodel = build_viewmodel(graph, report, max_depth=args.max_depth)
    write_html(viewmodel, args.output)
    if args.open_browser:
        import webbrowser  # noqa: PLC0415 - only needed for --open

        webbrowser.open(args.output.resolve().as_uri())


def run() -> None:
    sys.exit(main())
