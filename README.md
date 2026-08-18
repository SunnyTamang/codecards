# codecards

Read a codebase and get one interactive HTML file: functions as cards that
become their real source as you zoom in, calls as directed edges that leave
from the line making the call, and a player that walks an entry point end to
end. Built for the moment you open an unfamiliar repo and need a mental model
fast.

Calls are resolved by a language server when one is installed, which needs no
index and can never be stale. Without one, calls are matched by name and every
edge is marked as the guess it is. See [How calls are resolved](#how-calls-are-resolved).

No web service, no browser extension, no account. One file you can open
offline, commit, or hang off a pull request.

![The opening view: modules as ruled plates on a coordinate field, sized by how
many callers each has](screenshots/overview.webp)

*codecards run over its own source. Size is fan-in, so `graph` reads as
load-bearing before you have read a single name.*

## Install

```bash
pip install git+https://github.com/SunnyTamang/codecards.git
```

Or from a checkout:

```bash
git clone https://github.com/SunnyTamang/codecards.git
cd codecards
pip install .
```

Requires Python 3.10 or newer. The tree-sitter parsers come with it.

For a resolved rather than a guessed graph, install the language server for
whatever you are reading:

```bash
pip install pyright                              # Python
go install golang.org/x/tools/gopls@latest       # Go
```

codecards finds these on `PATH`, and also in Neovim's Mason directory, so an
editor that already works needs nothing further.

## Use

```bash
codecards ./src -o graph.html --open
```

Open the file in any browser. It works offline and can be attached to a pull
request.

| Flag | Meaning |
| --- | --- |
| `-o, --output PATH` | output file (default: `codecards.html`) |
| `--exclude PATTERN` | glob to skip, repeatable |
| `--include-external` | draw stdlib and third-party targets as leaf nodes |
| `--no-source` | omit embedded source; cards stop above the card tier |
| `--max-depth N` | walkthrough depth cap (default: 15) |
| `--open` | open the result in your browser |
| `--quiet` | suppress the summary report |
| `--scip INDEX` | resolve calls from a SCIP index you built beforehand |
| `--lsp` | require a language server; fail rather than fall back to guessing |
| `--no-lsp` | never start a language server |
| `--version` | print the version and exit |

## How calls are resolved

Three ways to turn a call site into a target. Structure always comes from
tree-sitter; these differ only in who answers "what does this name refer to".

| | Setup | Result |
| --- | --- | --- |
| **Language server** (default) | install the server | resolved, nothing guessed, never stale |
| **SCIP index** (`--scip`) | build an index first | resolved, and portable: a file, so the same graph builds in CI or on anyone else's machine |
| **Name matching** (fallback) | none | every edge marked `inferred` or `ambiguous`, because every edge is a guess |

| Language | Server | Indexer |
| --- | --- | --- |
| Python | `pip install pyright` | [scip-python](https://github.com/sourcegraph/scip-python) |
| Go | `go install golang.org/x/tools/gopls@latest` | [scip-go](https://github.com/scip-code/scip-go) |
| anything tree-sitter parses | - | - |

The last row gets name matching only. That is a real graph and a guessed one:
useful for finding your way around, not for trusting a particular edge.

codecards prints which server it started. If none is installed it says so,
names the one this project would want, and draws the guessed graph anyway.

**What an index buys that a server does not.** A call through an interface has
no single answer in the source - `p.renderer.flush()` runs whichever type is
behind `renderer` that day. A SCIP index records every type satisfying the
interface, so the call is drawn to the interface method as `resolved` and to
each implementation as `ambiguous`: at runtime it is one of these, and here
they are by name. gopls can answer this too; pyright cannot.

**What a server buys that an index does not.** An index describes the code as
it was when the indexer ran. If any file it covers has been edited since,
codecards says so, names the files, prints the command that rebuilds it, and
draws the graph anyway. A server reads the working tree, so the question never
arises.

Building an index, if you want one:

```bash
go install github.com/scip-code/scip-go/cmd/scip-go@latest
cd ./your-project && "$(go env GOPATH)/bin/scip-go" index ./... --output index.scip
codecards ./your-project --scip index.scip -o graph.html --open
```

The quoted `$(go env GOPATH)/bin` is deliberate: `go install` writes there, and
that directory is not on `PATH` unless you put it there yourself. Run the
indexer from the environment the project builds in - an indexer resolves
imports through the toolchain it can see, and outside that environment it exits
successfully having resolved far less.

## Reading the graph

The view opens on collapsed modules. Click a card to expand it, double-click to
collapse. Pick an entry point and press play to walk the call sequence.

Cards are laid out by call direction: a caller always sits above its callees, so
vertical position is call depth rather than something you have to read off the
arrowheads.

Size is fan-in. A callable is drawn larger the more places call it, so the
functions carrying the program read before you have read a single name. The key
in the bottom-left corner shows the ramp.

### Zoom changes what a card shows

| Zoom | You see |
| --- | --- |
| Far out | Names at display size, drawn larger the more callers they have. The architecture, nothing else |
| Middle | Name, signature, docstring line, and fan-in / fan-out counts |
| Close in | Point at a card and it opens its real source, syntax highlighted, with line numbers |

Only the card you point at, select, or pin opens its source. Opening every card
at once buries the graph under overlapping panels, so zoom decides how much
detail is *available* and you decide which card spends the space.

Pin a card to hold it open while you zoom back out, so you can read one
function without losing sight of the whole map.

![A pinned card showing its real source, syntax highlighted, with the lines
that make calls marked in the gutter](screenshots/source.webp)

At source zoom, a call marks the line that makes it, and the gutter says whether
that call sits inside a conditional or a loop. Knowing a call happens only
sometimes, or many times, is most of what understanding a flow means.

Select a card to dim everything outside its neighbourhood, and widen the radius
to answer "what does this touch, and what would I break by changing it".
Selecting a module keeps everything inside it lit, since the question is about
that whole region.

Each card carries an info control that opens a panel listing its callers, its
calls, and a link into your editor. It stays shut until you ask for it, and
once open it follows whatever you select next.

Ways into the program carry an `entry` chip: a `__main__` block, a console
script, or a framework decorator such as a route. Tests and functions that
merely have no callers are left unmarked, since marking those marks half the
canvas.

Cards nothing calls carry an `unused` badge, so you can see which parts of the
map you are free to ignore. Methods the language calls for you are exempt: a
`__eq__` runs on every `==` and a property on every attribute access, so
neither is dead code however few call sites point at it.

Special methods are hidden by default, since they are machinery rather than
flow. The `Dunders` toggle brings them back. Hiding them does not lose
anything: a call into `__init__` re-points at the class, so you still see what
builds what.

Edges are drawn according to how certain the analysis is:

| Style | Tier | Meaning |
| --- | --- | --- |
| Solid | resolved | The target is statically determined - an import, module scope, `self` through the MRO, an annotated type, or a local constructor |
| Dashed | inferred | An attribute call with no type information, but exactly one function in the codebase has that name |
| Hidden by default | ambiguous | Several functions share that name, so no single target is trusted. Toggle them on to see the candidates |

The `i` button in the toolbar lists what every style on the canvas means,
alongside the resolution figures.

Every run prints its resolution rate. A high rate means the picture is
trustworthy; a low one means the codebase leans on dynamic dispatch and you
should read the dashed edges with suspicion.

### Walking an entry point

Pick a way in and press play. The trail along the top is where you are, the
caption names the call being made and the line making it, and the card being
called is marked while the step lasts.

![The walkthrough player mid-run, with the call chain along the top and the
calling line highlighted inside the open card](screenshots/walkthrough.webp)

## What static analysis cannot see

Some things no amount of reading decides, and they are reported as unresolved
and counted in the summary rather than guessed:

- Dynamic dispatch through `getattr(obj, name)()`
- Function registries and dispatch dictionaries
- Monkey-patched attributes
- Metaclass-generated methods

The list is shorter than it used to be. A language server carries type
information, so calls on the return value of an unannotated function resolve,
and so does `super(B, self).method()` - which starts its search *after* `B`,
and which reading the source alone can only refuse.

A call through an interface resolves to every type implementing it, marked
`ambiguous`. That needs gopls or a SCIP index; pyright does not answer it.

Work that happens because a module was imported - decorator registration,
dispatch tables built at import time, plugin discovery - is not drawn at all
yet, whichever resolver is running. `examples/pipeline` is a worked example of
what that costs.

The walkthrough ordering is lexical, not executional: a call inside an `if`
still gets a step. Steps sitting inside a conditional or a loop are labelled as
such. This is a map of what the code *can* do, not a trace of one run.

## Development

```bash
pip install -e ".[dev]"
python -m playwright install chromium
python -m pytest
```

Browser tests need Chromium; they skip cleanly if Playwright is absent.

**The resolution ratchet.** `tests/test_corpus.py` runs codecards over its own
source and asserts the share of calls it resolves confidently stays above
`MIN_RESOLUTION_RATE`. Raise that constant when you improve the resolver. Never
lower it to make a build pass: a drop means resolution regressed, and the number
is the only thing that will tell you. pyright is in the dev extra so this
always runs - a ratchet that skips when a dependency is missing is not a
ratchet.

**The golden cross-checks.** Two algorithms exist in both Python and JavaScript:
collapse and the walkthrough. The Python versions are the specification, and the
generated page carries one golden output of each. `tests/render/test_goldens.py`
asserts the browser reproduces them. If it fails, work out which side is wrong;
do not regenerate the golden.

**See the tool's view of itself:**

```bash
python -m codecards src/codecards -o self.html --open
```
