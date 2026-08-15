from __future__ import annotations

import socket

import pytest

import math_agent.net_safety as net_safety
from math_agent.net_safety import (
    UnsafeFetchURL,
    fetch_public_url,
    is_blocked_ip,
    normalize_ip,
    normalize_public_https_url,
    validate_public_http_url,
    validate_public_https_url,
)


@pytest.mark.asyncio
async def test_validate_public_http_url_rejects_non_http():
    with pytest.raises(UnsafeFetchURL, match="http/https"):
        await validate_public_http_url("file:///tmp/source.txt")


@pytest.mark.asyncio
async def test_validate_public_http_url_rejects_loopback():
    with pytest.raises(UnsafeFetchURL, match="private or reserved"):
        await validate_public_http_url("http://127.0.0.1:8000/")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://user:secret@api.example.com/v1",
        "https://api.example.com/v1?token=secret",
        "https://api.example.com/v1#fragment",
        "https://api.example.com:invalid/v1",
    ],
)
def test_normalize_public_https_url_rejects_unsafe_structure(url):
    with pytest.raises(UnsafeFetchURL):
        normalize_public_https_url(url)


def test_normalize_public_https_url_strips_outer_space_and_trailing_slash():
    assert (
        normalize_public_https_url("  https://api.example.com/v1/  ")
        == "https://api.example.com/v1"
    )


@pytest.mark.asyncio
async def test_validate_public_https_url_rejects_loopback():
    with pytest.raises(UnsafeFetchURL, match="private or reserved"):
        await validate_public_https_url("https://127.0.0.1/v1")


@pytest.mark.asyncio
async def test_validate_public_https_url_rejects_non_global_resolved_address(
    monkeypatch,
):
    monkeypatch.setattr(
        "math_agent.net_safety._validate_host", lambda _host: ["2001:db8::1"]
    )

    with pytest.raises(UnsafeFetchURL, match="non-public"):
        await validate_public_https_url("https://api.example.com/v1")


def test_normalize_ip_maps_ipv4_mapped_ipv6():
    assert str(normalize_ip("::ffff:127.0.0.1")) == "127.0.0.1"
    assert str(normalize_ip("::ffff:10.0.0.1")) == "10.0.0.1"
    assert str(normalize_ip("::1")) == "::1"
    assert str(normalize_ip("8.8.8.8")) == "8.8.8.8"


def test_normalize_ip_rejects_bad_input():
    with pytest.raises(ValueError):
        normalize_ip("not-an-ip")


@pytest.mark.parametrize(
    "address",
    [
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "::",
        "ff02::1",
    ],
)
def test_is_blocked_ip_blocks_mapped_unspecified_and_multicast(address):
    assert is_blocked_ip(address)
    assert net_safety._blocked_ip(address)


def test_is_blocked_ip_blocks_unparseable_and_allows_public():
    assert is_blocked_ip("not-an-ip")
    assert not is_blocked_ip("8.8.8.8")
    assert not is_blocked_ip("2606:4700:4700::1111")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://[::ffff:169.254.169.254]/latest/meta-data",
        "http://[::]/",
        "http://[ff02::1]/",
    ],
)
async def test_validate_public_http_url_rejects_mapped_unspecified_multicast(url):
    with pytest.raises(UnsafeFetchURL, match="private or reserved"):
        await validate_public_http_url(url)


@pytest.mark.asyncio
async def test_validate_public_http_url_rejects_mapped_dns_result(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("::ffff:10.0.0.1", 0, 0, 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(UnsafeFetchURL, match="private or reserved"):
        await validate_public_http_url("http://evil.example.com/")


class _FakeStreamResponse:
    def __init__(self, status_code, headers, url):
        self.status_code = status_code
        self.headers = headers
        self.url = url
        self.is_redirect = status_code in (301, 302, 303, 307, 308)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self):
        yield b""

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    def __init__(self, responses, **kwargs):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None):
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_fetch_public_url_rejects_redirect_to_mapped_address(monkeypatch):
    import httpx

    def fake_getaddrinfo(host, port, type=0):
        if host == "public.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("::ffff:169.254.169.254", 0, 0, 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    redirect = _FakeStreamResponse(
        302,
        httpx.Headers({"location": "http://internal.example.com/latest/meta-data"}),
        httpx.URL("http://93.184.216.34/"),
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient([redirect])
    )

    with pytest.raises(UnsafeFetchURL, match="private or reserved"):
        await fetch_public_url("http://public.example.com/", timeout_seconds=5.0)
