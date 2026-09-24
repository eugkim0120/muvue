"""P8 acceptance (plan section 11 row P8): "Same `index.html` renders" and
"SSE connects to the daemon".

The P8 VS Code extension (`vscode-extension/`) never bundles a copy of the
dashboard: its webview is an `<iframe>` pointed straight at the running
`muvue serve` daemon's own URL (`vscode-extension/src/lib.ts`
`buildWebviewHtml`), and its bridge code talks to the same daemon HTTP API
P2-P7 already built. There is no live VS Code Extension Host available in
this environment (see `vscode-extension/README.md`), so this test proves
the two acceptance criteria the extension actually depends on against a
*real* `muvue serve` subprocess, the same pattern `test_serve_integration.py`
uses:

1. `GET /` on the daemon returns exactly the `index.html` file shipped in
   `src/muvue/api/static/index.html` -- byte-identical, not a snapshot --
   which is what the extension's iframe would load.
2. `GET /events/stream` on that same daemon is a live SSE endpoint: it
   responds with `text/event-stream` and actually emits a `data:` line,
   which is what `index.html`'s own `EventSource("/events/stream")` (see
   that file) connects to once loaded inside the iframe.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from muvue.core.repo_init import init_repo

STATIC_INDEX = Path(__file__).resolve().parents[1] / "src" / "muvue" / "api" / "static" / "index.html"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_daemon(proc: subprocess.Popen, deadline_s: float = 20) -> None:
    deadline = time.monotonic() + deadline_s
    lines: list[str] = []
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        lines.append(line)
        if "listening on" in line:
            return
    raise AssertionError(f"daemon never printed its readiness line; output so far: {lines}")


def _urlopen_with_retry(url: str, *, deadline_s: float = 10, **kwargs):
    """The "listening on" readiness line (printed just before
    `uvicorn.run` blocks) can race the socket actually accepting
    connections; retry briefly instead of sleeping a fixed amount."""
    deadline = time.monotonic() + deadline_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return urllib.request.urlopen(url, **kwargs)
        except urllib.error.URLError as err:
            last_err = err
            time.sleep(0.1)
    raise AssertionError(f"could not connect to {url}: {last_err}")


def test_daemon_root_serves_the_real_index_html_byte_identical(tmp_path: Path):
    repo_root = tmp_path
    init_repo(repo_root)
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo_root), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_daemon(proc)
        # This is exactly the URL vscode-extension/src/lib.ts's
        # `buildWebviewHtml` would point the webview's <iframe> at
        # (`muvue.daemonUrl`, default `http://127.0.0.1:8765`, here the
        # free test port instead).
        with _urlopen_with_retry(f"http://127.0.0.1:{port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")
            content_type = resp.headers.get("Content-Type", "")
        assert "html" in content_type.lower()
        assert body == STATIC_INDEX.read_text(), (
            "daemon's GET / must be byte-identical to the shipped index.html "
            "(P8 acceptance: 'same index.html renders', not a bundled copy)"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_daemon_events_stream_is_live_sse_the_loaded_page_can_connect_to(tmp_path: Path):
    repo_root = tmp_path
    init_repo(repo_root)
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo_root), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_daemon(proc)
        with _urlopen_with_retry(f"http://127.0.0.1:{port}/events/stream", timeout=10) as resp:
            assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
            first_line = resp.readline().decode("utf-8")
            assert first_line.startswith("data: "), f"expected an SSE data line, got: {first_line!r}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
