"""Network helpers for user-requested URL fetching."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import ParseResult, urljoin, urlparse


class UnsafeFetchURL(ValueError):
    """Raised when a requested fetch target is not safe for server-side access."""


@dataclass(frozen=True)
class SafeFetchResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        import httpx

        # aiter_bytes() already yields decompressed bodies, but upstream may still
        # advertise Content-Encoding. Drop it so httpx does not decompress twice.
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() != "content-encoding"
        }
        response = httpx.Response(
            self.status_code,
            headers=headers,
            content=self.content,
            request=httpx.Request("GET", self.url),
        )
        return response.text

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or self.url


# Explicit RFC 1918 / IANA special-purpose ranges we want to block.
# We do NOT use ip.is_private because Python 3.11+ expanded that definition
# to include 198.18.0.0/15 (RFC 2544 benchmarking), which is used by real
# public CDNs such as arXiv, causing false-positive SSRF blocks.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),        # RFC 1918 private
    ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918 private
    ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918 private
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("169.254.0.0/16"),     # link-local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique-local
    ipaddress.ip_network("0.0.0.0/8"),          # unspecified
    ipaddress.ip_network("100.64.0.0/10"),      # RFC 6598 shared address (carrier-grade NAT)
    ipaddress.ip_network("192.0.0.0/24"),       # RFC 6890 IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),       # RFC 5737 TEST-NET-1 (documentation)
    ipaddress.ip_network("198.51.100.0/24"),    # RFC 5737 TEST-NET-2 (documentation)
    ipaddress.ip_network("203.0.113.0/24"),     # RFC 5737 TEST-NET-3 (documentation)
    ipaddress.ip_network("240.0.0.0/4"),        # reserved
    ipaddress.ip_network("224.0.0.0/4"),        # multicast
]


def _blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _validate_host(host: str) -> list[str]:
    """Resolve *host* and return all public IP addresses.

    Raises ``UnsafeFetchURL`` when the host is a blocked literal or when any
    resolved address falls in a private/reserved range. Returning the resolved
    addresses lets callers pin the connection to one of them, closing the DNS
    rebinding window between validation and the HTTP request.
    """
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeFetchURL(f"Could not resolve URL host: {host}") from exc
        addresses = [info[4][0] for info in infos]
        if not addresses:
            raise UnsafeFetchURL(f"Could not resolve URL host: {host}")
        blocked = [addr for addr in addresses if _blocked_ip(addr)]
    else:
        addresses = [str(ip)]
        blocked = addresses if _blocked_ip(str(ip)) else []

    if blocked:
        raise UnsafeFetchURL("URL resolves to a private or reserved network address.")
    return addresses


def _host_header(parsed: ParseResult) -> str:
    """Return ``host[:port]`` for use as a Host header."""
    host = parsed.hostname or ""
    port = parsed.port
    if port is None:
        return host
    return f"{host}:{port}"


async def validate_public_http_url(url: str) -> str:
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeFetchURL("Only http/https URLs are supported.")
    if not parsed.hostname:
        raise UnsafeFetchURL("URL must include a host.")
    if parsed.username or parsed.password:
        raise UnsafeFetchURL("URLs with embedded credentials are not supported.")
    await asyncio.to_thread(_validate_host, parsed.hostname)
    return normalized


async def fetch_public_url(
    url: str,
    *,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
    max_bytes: int = 4 * 1024 * 1024,
    max_redirects: int = 5,
) -> SafeFetchResponse:
    """Fetch a public http/https URL, validating and pinning every target.

    For ``http`` URLs the hostname is replaced with a validated public IP and
    the original host is sent as a ``Host`` header. This pins the TCP
    connection to the address we validated, defeating DNS rebinding attacks
    that try to switch a public hostname to an internal address after
    validation.  ``https`` URLs are validated on every hop but left to httpx
    for TLS/SNI handling, where certificate verification provides additional
    protection against rebinding to arbitrary internal hosts.
    """
    import httpx

    current_url = await validate_public_http_url(url)
    request_headers = dict(headers or {})
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, headers=request_headers
    ) as client:
        for _ in range(max_redirects + 1):
            parsed = urlparse(current_url)
            resolved = await asyncio.to_thread(_validate_host, parsed.hostname)
            if parsed.scheme.lower() == "http":
                # Pin this request to the first validated public IP.
                ip = resolved[0]
                netloc = f"{ip}:{parsed.port}" if parsed.port else ip
                current_url = parsed._replace(netloc=netloc).geturl()
                request_headers["Host"] = _host_header(parsed)
            else:
                # HTTPS: keep the original hostname so SNI/certificate
                # validation work correctly; resolution was just validated.
                request_headers.pop("Host", None)

            async with client.stream(
                "GET", current_url, headers=request_headers
            ) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise UnsafeFetchURL(
                            "Redirect response did not include a Location header."
                        )
                    current_url = urljoin(str(resp.url), location)
                    continue

                content = bytearray()
                async for chunk in resp.aiter_bytes():
                    if len(content) + len(chunk) > max_bytes:
                        raise UnsafeFetchURL(
                            "Response body is too large to fetch safely."
                        )
                    content.extend(chunk)
                resp.raise_for_status()
                return SafeFetchResponse(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    headers=dict(resp.headers),
                    content=bytes(content),
                )

    raise UnsafeFetchURL("Too many redirects while fetching URL.")
