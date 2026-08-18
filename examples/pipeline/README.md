# pipeline

A small text-checking program, written to be a worked example of one thing:
**work that happens because a module was imported, rather than because a
function was called.**

Run it:

```bash
cd examples
python -m pipeline some-file.txt
```

Graph it:

```bash
codecards examples/pipeline -o pipeline.html --open
```

## What each file is here for

| File | Demonstrates |
| --- | --- |
| `__main__.py` | An entry point that defines its own functions *and* calls into other modules. `if __name__ == "__main__"` calls `main`, at module scope |
| `config.py` | `SETTINGS = _from_environment(DEFAULT_LIMITS)` runs on import. No function in the program calls `_from_environment` |
| `registry.py` | A dispatch table written at import time, read at run time. `run()` calls through it, which is honestly unresolvable |
| `checks/__init__.py` | `from . import length, naming` exists purely for its side effect. Deleting it silently removes both checks |
| `checks/length.py` | `@register("length")` calls `register` while the module is being imported |
| `checks/naming.py` | The same, plus `re.compile(...)` at module scope |
| `report.py` | An ordinary class whose methods call each other, as a control |

## What codecards makes of it today

```
31 calls: 7 resolved, 2 inferred, 14 external, 8 unresolved
14 callables, 9 edges drawn
```

All nine edges live inside `__main__` and `report`. Six calls written at module
scope produce no edge at all, because an edge must start at a callable and
module-level code has no calling function to name.

The consequence is not that the picture is incomplete. It is that the picture
is **wrong**. These five cards are badged `unused`:

```
pipeline.checks.length.check_length
pipeline.checks.naming.check_naming
pipeline.config._from_environment
pipeline.registry.register
pipeline.registry.register.bind
```

Those are the checks the program exists to run, the decorator that installs
them, and the settings loader they read. A new joiner handed this graph would
conclude the `checks` package is dead code and delete it.

This directory exists to be the fixture that change is measured against.

## What should stay unresolved

`registry.run` calls `CHECKS[name](lines)`. The target depends on which modules
were imported, which is a runtime fact, and it must stay undrawn.

It is not merely unresolved: the thing being called is a subscript rather than
a name, so there is no identifier to look up and the call is not counted at
all. That is the honest answer either way, and it sits here next to the cases
that were missing only because nothing modelled import-time flow.
