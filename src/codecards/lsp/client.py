"""Just enough of the Language Server Protocol to ask where a name is defined.

This speaks the protocol directly rather than through a library, for the same
reason `scip/index.py` reads protobuf by hand: one request and two
notifications is a smaller thing to own than a dependency, and every failure
mode stays visible.

A language server is a subprocess that outlives many requests, so the parts
that need care are the ones that go wrong quietly. A server that never replies
must time out rather than hang. A server that dies must say so rather than
leave the caller waiting. And a server that asks the client a question - which
pyright does, twice, before it will answer anything - must be answered, or it
sits waiting for a reply that never comes while the caller sits waiting for a
definition that never arrives.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

#: Where a server binary might live besides PATH. Neovim's package manager
#: installs into its own directory, which is not on PATH for anything but
#: Neovim - and someone with a working editor setup has already installed
#: exactly the servers this wants.
EXTRA_BIN_DIRS = (
    Path.home() / ".local/share/nvim/mason/bin",
)

#: A first request may arrive while the server is still reading the project.
#: gopls on a cold module and pyright on a large tree both take seconds.
STARTUP_TIMEOUT = 120.0

#: After that, a request that has not answered is a bug or a hang.
REQUEST_TIMEOUT = 30.0


class ServerUnusable(RuntimeError):
    """The server could not be started, died, or would not answer."""


def find(command: tuple[str, ...]) -> list[str] | None:
    """Resolve a server command against PATH and the editor install dirs."""
    if not command:
        return None
    found = shutil.which(command[0])
    if found is None:
        for directory in EXTRA_BIN_DIRS:
            candidate = directory / command[0]
            if candidate.is_file() and os.access(candidate, os.X_OK):
                found = str(candidate)
                break
    return None if found is None else [found, *command[1:]]


class Client:
    """One server subprocess, speaking JSON-RPC over its stdio."""

    def __init__(self, command: list[str], root: Path):
        self.root = root
        self.command = command
        self._next_id = 0
        self._replies: dict[int, dict] = {}
        self._lock = threading.Condition()
        self._died: str | None = None
        try:
            self._proc = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=str(root))
        except OSError as exc:
            raise ServerUnusable(f"could not start {command[0]}: {exc}") from exc
        threading.Thread(target=self._read_forever, daemon=True).start()

    # -- wire ---------------------------------------------------------------

    def _write(self, message: dict) -> None:
        body = json.dumps(message).encode()
        try:
            self._proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise ServerUnusable(f"{self.command[0]} closed its input") from exc

    def _read_forever(self) -> None:
        try:
            while True:
                length = None
                while True:
                    line = self._proc.stdout.readline()
                    if not line:
                        raise EOFError
                    if line in (b"\r\n", b"\n"):
                        break
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":")[1])
                if length is None:
                    raise EOFError("header with no content-length")
                message = json.loads(self._proc.stdout.read(length))
                self._dispatch(message)
        except Exception as exc:  # noqa: BLE001 - the thread's job is to record why
            with self._lock:
                self._died = f"{type(exc).__name__}: {exc}"
                self._lock.notify_all()

    def _dispatch(self, message: dict) -> None:
        if "id" in message and ("result" in message or "error" in message):
            with self._lock:
                self._replies[message["id"]] = message
                self._lock.notify_all()
            return
        if "id" in message and "method" in message:
            # A request from the server. It blocks until answered, and pyright
            # will not resolve a single position until its configuration
            # request has come back - which is what made the spike look like
            # a server that simply never replied.
            if message["method"] == "workspace/configuration":
                items = message.get("params", {}).get("items", [])
                result: Any = [{} for _ in items]
            else:
                result = None
            self._write({"jsonrpc": "2.0", "id": message["id"], "result": result})

    # -- protocol -----------------------------------------------------------

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float = REQUEST_TIMEOUT) -> dict:
        self._next_id += 1
        want = self._next_id
        self._write({"jsonrpc": "2.0", "id": want, "method": method, "params": params})
        with self._lock:
            ok = self._lock.wait_for(
                lambda: want in self._replies or self._died is not None, timeout)
            if want in self._replies:
                return self._replies.pop(want)
            if self._died is not None:
                raise ServerUnusable(f"{self.command[0]} stopped: {self._died}")
            if not ok:
                raise ServerUnusable(
                    f"{self.command[0]} did not answer {method} within "
                    f"{timeout:.0f}s")
        raise ServerUnusable(f"{self.command[0]} did not answer {method}")

    def initialize(self) -> dict:
        uri = self.root.as_uri()
        reply = self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": uri,
            "rootPath": str(self.root),
            "workspaceFolders": [{"uri": uri, "name": self.root.name}],
            "initializationOptions": {},
            "capabilities": {
                "workspace": {
                    "workspaceFolders": True,
                    "configuration": True,
                    "didChangeConfiguration": {"dynamicRegistration": True},
                },
                "textDocument": {
                    "synchronization": {"dynamicRegistration": True},
                    "definition": {"linkSupport": True},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                },
            },
        }, timeout=STARTUP_TIMEOUT)
        if "error" in reply:
            raise ServerUnusable(
                f"{self.command[0]} refused to initialize: "
                f"{reply['error'].get('message')}")
        self.notify("initialized", {})
        # pyright answers nothing at all until it has been given settings.
        self.notify("workspace/didChangeConfiguration", {"settings": {
            "python": {"analysis": {"autoSearchPaths": True,
                                    "useLibraryCodeForTypes": True,
                                    "diagnosticMode": "workspace"}}}})
        return reply["result"].get("capabilities", {})

    def open(self, path: Path, language_id: str) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": path.as_uri(), "languageId": language_id,
            "version": 1, "text": text}})

    def close(self) -> None:
        try:
            self.request("shutdown", {}, timeout=5)
            self.notify("exit", {})
        except (ServerUnusable, OSError):
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
