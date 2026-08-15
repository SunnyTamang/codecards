"""Reading a SCIP index.

Protobuf's wire format carries field numbers rather than names, so the handful
of numbers this needs is enough to read an index without generated bindings or
a protobuf dependency. The schema is stable and small where it matters:

    Index      { metadata = 1, documents = 2, external_symbols = 3 }
    Document   { relative_path = 1, occurrences = 2, symbols = 3, language = 4 }
    Occurrence { range = 1, symbol = 2, symbol_roles = 3 }

A range is [start_line, start_char, end_line, end_char], or three values when
the occurrence sits on one line. Lines and characters are zero based.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Bit 0 of symbol_roles. Everything else is a reference to a definition
#: recorded somewhere else, possibly in another file.
DEFINITION = 1


@dataclass(frozen=True)
class Occurrence:
    line: int          # zero based
    start_char: int
    end_char: int
    symbol: str
    is_definition: bool

    @property
    def is_local(self) -> bool:
        """Locals are numbered per file and mean nothing outside it."""
        return self.symbol.startswith("local ")


@dataclass(frozen=True)
class Document:
    path: str
    occurrences: tuple[Occurrence, ...]


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, wire_type, payload) for one message."""
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = _varint(buf, i)
            yield number, wire, value
        elif wire == 2:
            length, i = _varint(buf, i)
            yield number, wire, buf[i:i + length]
            i += length
        elif wire == 5:
            yield number, wire, buf[i:i + 4]
            i += 4
        elif wire == 1:
            yield number, wire, buf[i:i + 8]
            i += 8
        else:  # pragma: no cover - not emitted by any SCIP indexer
            raise ValueError(f"unsupported protobuf wire type {wire}")


def _packed(buf: bytes) -> list[int]:
    out: list[int] = []
    i = 0
    while i < len(buf):
        value, i = _varint(buf, i)
        out.append(value)
    return out


def read(path: Path) -> list[Document]:
    raw = Path(path).read_bytes()
    documents: list[Document] = []

    for number, _wire, payload in _fields(raw):
        if number != 2 or not isinstance(payload, bytes):
            continue
        doc_path = ""
        occurrences: list[Occurrence] = []

        for dn, dwire, dpayload in _fields(payload):
            if dn == 1 and dwire == 2:
                doc_path = dpayload.decode("utf-8", "replace")
            elif dn == 2 and dwire == 2:
                span: list[int] = []
                symbol = ""
                roles = 0
                for on, owire, opayload in _fields(dpayload):
                    if on == 1 and owire == 2:
                        span = _packed(opayload)
                    elif on == 1 and owire == 0:
                        span.append(opayload)
                    elif on == 2 and owire == 2:
                        symbol = opayload.decode("utf-8", "replace")
                    elif on == 3 and owire == 0:
                        roles = opayload
                if not span:
                    continue
                # Three values means the occurrence begins and ends on one
                # line, which is the common case; four spells the end line out.
                end_char = span[2] if len(span) == 3 else span[3]
                occurrences.append(Occurrence(
                    line=span[0], start_char=span[1], end_char=end_char,
                    symbol=symbol, is_definition=bool(roles & DEFINITION),
                ))

        documents.append(Document(path=doc_path, occurrences=tuple(occurrences)))

    return documents


def is_empty(documents: list[Document]) -> bool:
    """An indexer that could not load the project writes a valid, empty index.

    It exits successfully and warns on stderr, so a caller that trusts the
    exit code reports a healthy repository as having no code in it at all.
    """
    return not any(doc.occurrences for doc in documents)


# -- symbol strings ---------------------------------------------------------
#
# A SCIP symbol is structured, not opaque:
#
#   scip-python python comp-harness 0.1.0 `comp_graph.llm.client`/LLMClient#complete_parsed().
#   └ scheme    └ lang  └ package   └ ver └ descriptors
#
# Descriptor suffixes carry the kind: `/` a namespace, `#` a type, `().` a
# method, `.` a term, `(x)` a parameter.


@dataclass(frozen=True)
class Symbol:
    package: str
    version: str
    descriptors: tuple[str, ...]   # e.g. ("comp_graph.llm.client", "LLMClient", "complete_parsed")
    kinds: tuple[str, ...]         # matching: ("namespace", "type", "method")

    @property
    def is_external(self) -> bool:
        return self.package in ("python-stdlib",) or self.version == "."


def parse(symbol: str) -> Symbol | None:
    """Split a symbol into its package and its descriptor chain."""
    if not symbol or symbol.startswith("local "):
        return None
    parts = symbol.split(" ", 4)
    if len(parts) < 5:
        return None
    _scheme, _language, package, version, rest = parts

    descriptors: list[str] = []
    kinds: list[str] = []
    token = ""
    i = 0
    while i < len(rest):
        char = rest[i]
        if char == "`":                    # a backtick-quoted name may hold dots
            end = rest.find("`", i + 1)
            if end == -1:
                break
            token += rest[i + 1:end]
            i = end + 1
            continue
        if char in "/#.":
            descriptors.append(token)
            kinds.append({"/": "namespace", "#": "type", ".": "term"}[char])
            token = ""
        elif char == "(" and rest[i:i + 3] == "().":
            descriptors.append(token)
            kinds.append("method")
            token = ""
            i += 2
        else:
            token += char
        i += 1

    if not descriptors:
        return None
    return Symbol(package=package, version=version,
                  descriptors=tuple(descriptors), kinds=tuple(kinds))
