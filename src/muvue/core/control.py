"""Local control socket for a running `muvue serve`.

`muvue link` asks it for a fresh one-time dashboard link, so an expired
or lost session can be recovered without restarting the daemon (which
would also kill every link already handed out). It is a Unix socket next
to the port file: mode 0600 in a 0700 directory, and on Linux the peer's
uid must match ours. Only the user who runs the daemon can ask. The
socket holds no secret; the token stays in the daemon's memory and is
only sent back over this same-user channel (decision #173)."""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
from pathlib import Path

from .daemon import SessionManager, port_file_path


class LinkError(RuntimeError):
    pass


def socket_path(repo_root: Path) -> Path:
    return port_file_path(repo_root).with_suffix(".sock")


def _peer_is_self(conn: socket.socket) -> bool:
    """Linux reports the connecting process's credentials; elsewhere the
    socket's 0600 mode is the whole check."""
    if not hasattr(socket, "SO_PEERCRED"):
        return True
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid == os.getuid()


class ControlServer:
    def __init__(self, path: Path, session: SessionManager, *, base_url: str) -> None:
        self.path = path
        self.session = session
        self.base_url = base_url.rstrip("/")
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.unlink(missing_ok=True)  # a crashed daemon's leftover
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)  # created 0600, never briefly wider
        try:
            sock.bind(str(self.path))
        finally:
            os.umask(old_umask)
        sock.listen(4)
        sock.settimeout(0.5)  # lets the loop notice stop() without closing under accept()
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, name="muvue-control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                print(f"muvue control socket stopped: {exc}", file=sys.stderr)
                return
            with conn:
                try:
                    self._handle(conn)
                except OSError as exc:
                    print(f"muvue control socket: a client request failed: {exc}", file=sys.stderr)

    def _reply(self, conn: socket.socket, payload: dict) -> None:
        conn.sendall(json.dumps(payload).encode() + b"\n")

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        if not _peer_is_self(conn):
            self._reply(conn, {"error": "refused: the control socket only answers the user running the daemon"})
            return
        request = conn.makefile("r").readline().strip()
        if request != "link":
            self._reply(conn, {"error": f"unknown request {request!r}"})
            return
        nonce, renewed = self.session.relink()
        self._reply(conn, {"url": f"{self.base_url}/#n={nonce}", "token": self.session.token, "renewed": renewed})


def request_link(path: Path, *, timeout: float = 5.0) -> dict:
    """Ask the daemon behind `path` for a fresh link. Raises LinkError
    with a message fit to show a user."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise LinkError(f"nothing is answering at {path} ({exc.strerror})") from exc
        s.sendall(b"link\n")
        line = s.makefile("r").readline()
    if not line:
        raise LinkError("the daemon closed the connection without replying")
    reply = json.loads(line)
    if "error" in reply:
        raise LinkError(str(reply["error"]))
    if not (isinstance(reply.get("url"), str) and isinstance(reply.get("token"), str) and isinstance(reply.get("renewed"), bool)):
        raise LinkError(f"unexpected reply from the daemon: {line.strip()[:200]}")
    return reply
