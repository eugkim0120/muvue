"""Spec A: tailnet identity resolver (`core.tailnet`). The transport is
injected, so none of this touches a real tailscaled."""

from __future__ import annotations

import logging

import pytest

from muvue.core.tailnet import NotATailnetPeer, TailnetError, TailnetResolver


def _whois(login="me@example.com", user=7, profile_id=7, tags=None):
    return {"Node": {"User": user, "Tags": tags}, "UserProfile": {"ID": profile_id, "LoginName": login}}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_resolves_login_for_user_owned_node():
    r = TailnetResolver(lambda ip: _whois())
    peer = r.peer("100.64.1.2")
    assert peer is not None and peer.login == "me@example.com"


def test_tagged_node_is_not_a_person():
    r = TailnetResolver(lambda ip: _whois(tags=["tag:ci"]))
    assert r.peer("100.64.1.2") is None


def test_shared_in_node_with_foreign_profile_is_rejected():
    r = TailnetResolver(lambda ip: _whois(user=7, profile_id=9))
    assert r.peer("100.64.1.2") is None


def test_malformed_response_is_rejected_and_logged(caplog):
    r = TailnetResolver(lambda ip: {"Node": {}})
    with caplog.at_level(logging.WARNING):
        assert r.peer("100.64.1.2") is None
    assert "whois" in caplog.text


def test_not_a_peer_is_none_and_not_logged(caplog):
    def transport(ip):
        raise NotATailnetPeer(ip)

    r = TailnetResolver(transport)
    with caplog.at_level(logging.WARNING):
        assert r.peer("127.0.0.1") is None
    assert caplog.text == ""


def test_transport_failure_is_none_logged_once_per_reason(caplog):
    clock = Clock()

    def transport(ip):
        raise TailnetError("socket missing")

    r = TailnetResolver(transport, clock=clock)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert r.peer("100.64.1.2") is None
            clock.now += 10
    assert caplog.text.count("socket missing") == 1


def test_success_cached_for_ttl_then_refetched():
    clock, calls = Clock(), []

    def transport(ip):
        calls.append(ip)
        return _whois()

    r = TailnetResolver(transport, ttl_s=60, clock=clock)
    r.peer("100.64.1.2")
    r.peer("100.64.1.2")
    assert len(calls) == 1
    clock.now += 61
    r.peer("100.64.1.2")
    assert len(calls) == 2


def test_failure_is_retried_soon_not_cached_for_full_ttl():
    clock, calls = Clock(), []

    def transport(ip):
        calls.append(ip)
        raise TailnetError("boom")

    r = TailnetResolver(transport, ttl_s=60, clock=clock)
    r.peer("100.64.1.2")
    clock.now += 6
    r.peer("100.64.1.2")
    assert len(calls) == 2


def test_cache_is_bounded():
    r = TailnetResolver(lambda ip: _whois(), max_entries=4)
    for i in range(50):
        r.peer(f"100.64.0.{i}")
    assert len(r._cache) <= 4


@pytest.mark.parametrize("bad", ["", "not-an-ip", "100.64.1.2:80", "100.64.1.2\r\nHost: x"])
def test_non_ip_input_never_reaches_transport(bad):
    def transport(ip):
        raise AssertionError("transport must not be called")

    assert TailnetResolver(transport).peer(bad) is None


@pytest.mark.parametrize("outside", ["127.0.0.1", "192.168.1.5", "8.8.8.8", "::1"])
def test_addresses_outside_tailnet_ranges_never_reach_transport(outside):
    def transport(ip):
        raise AssertionError("transport must not be called")

    assert TailnetResolver(transport).peer(outside) is None
