"""Tailscale identity for the dashboard daemon.

`muvue serve` binds a tailnet address directly, so the TCP peer address is
the only identity input: headers such as X-Forwarded-For are never read here.
The peer is resolved through tailscaled's LocalAPI `whois` endpoint over its
Unix socket (stdlib only, no CLI fork per lookup).
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

log = logging.getLogger(__name__)

LOCALAPI_SOCKET = "/var/run/tailscale/tailscaled.sock"
_TIMEOUT_S = 2.0
_FAILURE_RETRY_S = 5.0


_TAILNET_RANGES = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def is_tailnet_host(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if getattr(addr, "scope_id", None):
        return False
    return any(addr in net for net in _TAILNET_RANGES)


class TailnetError(Exception):
    """whois could not be answered (socket missing, timeout, bad status)."""


class NotATailnetPeer(Exception):
    """tailscaled answered, and the address is not a tailnet node."""


@dataclass(frozen=True)
class TailnetPeer:
    login: str


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("local-tailscaled.sock", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _localapi_get(path: str, socket_path: str) -> tuple[int, bytes]:
    conn = _UnixHTTPConnection(socket_path, _TIMEOUT_S)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    except (OSError, http.client.HTTPException) as exc:
        raise TailnetError(f"tailscaled LocalAPI at {socket_path} unreachable: {exc}") from exc
    finally:
        conn.close()


def localapi_whois(ip: str, socket_path: str = LOCALAPI_SOCKET) -> dict:
    addr = ipaddress.ip_address(ip)
    hostport = f"[{addr}]:0" if addr.version == 6 else f"{addr}:0"
    status, body = _localapi_get(f"/localapi/v0/whois?{urlencode({'addr': hostport})}", socket_path)
    if status == 404:
        raise NotATailnetPeer(ip)
    if status != 200:
        raise TailnetError(f"tailscaled whois returned HTTP {status}")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise TailnetError(f"tailscaled whois returned invalid JSON: {exc}") from exc


def localapi_reachable(socket_path: str = LOCALAPI_SOCKET) -> None:
    """Raise TailnetError unless tailscaled's LocalAPI answers."""
    status, _ = _localapi_get("/localapi/v0/status", socket_path)
    if status != 200:
        raise TailnetError(f"tailscaled status returned HTTP {status}")


class TailnetResolver:
    def __init__(
        self,
        transport: Callable[[str], dict] = localapi_whois,
        *,
        ttl_s: float = 60.0,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, TailnetPeer | None]] = OrderedDict()
        self._logged: set[str] = set()
        self._lock = threading.Lock()

    def peer(self, ip: str) -> TailnetPeer | None:
        """The person behind `ip`, or None when it is not a user-owned tailnet node."""
        if not is_tailnet_host(ip):
            return None
        now = self._clock()
        with self._lock:
            hit = self._cache.get(ip)
        if hit is not None and hit[0] > now:
            return hit[1]
        peer, ttl = self._lookup(ip)
        with self._lock:
            self._cache.pop(ip, None)
            self._cache[ip] = (now + ttl, peer)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return peer

    def _lookup(self, ip: str) -> tuple[TailnetPeer | None, float]:
        try:
            data = self._transport(ip)
        except NotATailnetPeer:
            return None, self._ttl_s
        except TailnetError as exc:
            self._warn_once(str(exc))
            return None, _FAILURE_RETRY_S
        try:
            node, profile = data["Node"], data["UserProfile"]
            login, owner = profile["LoginName"], node["User"]
            profile_id = profile["ID"]
        except (KeyError, TypeError):
            self._warn_once("tailscaled whois response is missing Node/UserProfile fields")
            return None, _FAILURE_RETRY_S
        if node.get("Tags") or owner != profile_id or not isinstance(login, str) or not login:
            return None, self._ttl_s
        return TailnetPeer(login=login), self._ttl_s

    def _warn_once(self, reason: str) -> None:
        if reason not in self._logged:
            self._logged.add(reason)
            log.warning("tailnet identity unavailable: %s (falling back to token sign-in)", reason)
