"""Assemble the single self-contained HTML file.

Nothing may reference an external host: no CDN, no web fonts, no remote
images. Everything is inlined, which is what makes the output work offline
and survive being attached to a pull request.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"

#: Load order matters: pure logic, then the canvas, then view layers, then wiring.
JS_ORDER = (
    "collapse.js",
    "trace.js",
    "canvas.js",
    "cards.js",
    "edges.js",
    "graph-view.js",
    "panel.js",
    "player.js",
    "controls.js",
    "main.js",
)

VENDOR_ORDER = ("elk.bundled.js",)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _embed_json(payload: dict) -> str:
    """Serialise for inlining inside a <script> tag.

    `</script>` anywhere in the data would close the tag early, so every `</`
    is escaped. That is valid JSON string content and parses back identically.
    U+2028 and U+2029 are legal in JSON but terminate a JavaScript line, so
    they are escaped too. Embedded source makes both cases reachable with
    ordinary code rather than only with hostile input.
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (text.replace("</", "<\\/")
                .replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029"))


#: Matches every placeholder in the template. Substitution is one pass, on
#: purpose: see render_html.
_PLACEHOLDER = re.compile(r"/\*__CODECARDS_(CSS|VENDOR|DATA|APP)__\*/")


def render_html(viewmodel: dict) -> str:
    """Fill the template in a single pass.

    Sequential str.replace calls would rescan their own output, and the
    embedded data is arbitrary source text from the analysed project. When
    codecards analyses codecards, bundle.py's own source is embedded, and it
    contains the literal string for the APP placeholder. Replacing DATA first
    and APP second then injected raw JavaScript into the middle of a JSON
    string literal, producing a page that loaded elkjs fine and left
    window.CODECARDS_DATA undefined.

    One re.sub pass never revisits what it has already written, so the
    substitutions cannot see each other regardless of what the analysed
    project's source happens to contain.
    """
    parts = {
        "CSS": _read(ASSETS_DIR / "app.css"),
        "VENDOR": "\n;\n".join(
            _read(ASSETS_DIR / "vendor" / name) for name in VENDOR_ORDER
        ),
        "DATA": f"window.CODECARDS_DATA = {_embed_json(viewmodel)};\n",
        "APP": "\n;\n".join(_read(ASSETS_DIR / "js" / name) for name in JS_ORDER),
    }
    template = _read(ASSETS_DIR / "template.html")
    # A function replacement is used literally, so backslashes in the source
    # text are never interpreted as group references.
    return _PLACEHOLDER.sub(lambda match: parts[match.group(1)], template)


def write_html(viewmodel: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(viewmodel), encoding="utf-8")
