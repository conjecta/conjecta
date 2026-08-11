from __future__ import annotations

import hmac
import ipaddress
import os
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Deque

if TYPE_CHECKING:
    from fastapi import Request


def _http_exception(status_code: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


ACCESS_TOKEN_COOKIE = "conjecta_access_token"
DEFAULT_RATE_LIMIT_PER_MINUTE = 100
# Upper bound on tracked rate-limit keys. Keys embed the request path, so this
# caps memory an unauthenticated caller can cause the process to hold.
MAX_RATE_LIMIT_KEYS = 10_000


def configured_app_token() -> str | None:
    token = os.getenv("CONJECTA_AUTH_TOKEN") or os.getenv("CONJECTA_APP_TOKEN")
    return token.strip() if token and token.strip() else None


def _cookie_token(cookies: Any) -> str:
    if not cookies:
        return ""
    getter = getattr(cookies, "get", None)
    if not callable(getter):
        return ""
    return str(getter(ACCESS_TOKEN_COOKIE, "") or "").strip()


def _bearer_token(value: str | None) -> str:
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _valid_token(supplied: str, expected: str | None) -> bool:
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_ip(value: Any) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an address (optionally bracketed/with a numeric port) safely."""
    raw = str(value or "").strip()
    if not raw or "," in raw or "%" in raw:
        return None
    if raw.startswith("["):
        closing = raw.find("]")
        if closing < 0:
            return None
        suffix = raw[closing + 1 :]
        if suffix and not (suffix.startswith(":") and suffix[1:].isdigit()):
            return None
        raw = raw[1:closing]
    elif raw.count(":") == 1:
        possible_host, possible_port = raw.rsplit(":", 1)
        if "." in possible_host and possible_port.isdigit():
            raw = possible_host
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        return None


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw = os.getenv("CONJECTA_TRUSTED_PROXY_CIDRS", "")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in re.split(r"[\s,]+", raw.strip()):
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            # Invalid configuration entries never expand the trust boundary.
            continue
    return tuple(networks)


def is_trusted_proxy_client(client: Any) -> bool:
    peer = _parse_ip(getattr(client, "host", "") if client else "")
    if peer is None:
        return False
    return any(
        peer.version == network.version and peer in network
        for network in _trusted_proxy_networks()
    )


def trusted_proxy_header(headers: Any, client: Any, name: str) -> str:
    """Return a proxy header only when the direct socket peer is trusted."""
    if not is_trusted_proxy_client(client):
        return ""
    return str(headers.get(name, "") or "").strip()


def _client_host(headers: Any, client: Any) -> str:
    """Resolve auth/rate identity without trusting client-supplied forwarding chains."""
    peer_value = getattr(client, "host", "") if client else ""
    peer = _parse_ip(peer_value)
    if is_trusted_proxy_client(client):
        raw_real_ip = trusted_proxy_header(headers, client, "x-real-ip")
        real_ip = _parse_ip(raw_real_ip)
        if real_ip is not None:
            return str(real_ip)
        # A trusted loopback proxy is not the request identity. Missing or
        # malformed overwritten headers must fail closed for local-dev auth and
        # receive an explicit non-loopback rate identity.
        return "proxy-client-invalid" if raw_real_ip else "proxy-client-unknown"
    if peer is not None:
        return str(peer)
    return str(peer_value or "").strip().lower()


def _is_loopback_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    parsed = _parse_ip(host)
    return bool(parsed and parsed.is_loopback)


def _allow_unauthenticated(headers: Any, client: Any) -> bool:
    if _truthy_env("CONJECTA_ALLOW_UNAUTHENTICATED"):
        return True
    return _is_loopback_host(_client_host(headers, client))


def _looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and token.startswith("eyJ")


def _jwt_user_from_token(token: str):
    from math_agent.web.jwt_auth import decode_access_token

    return decode_access_token(token)


def _accept_supplied_token(supplied: str, *, log_admin_token: str | None = None) -> bool:
    expected = configured_app_token()
    if _valid_token(supplied, expected) or _valid_token(supplied, log_admin_token):
        return True
    if _looks_like_jwt(supplied) and _jwt_user_from_token(supplied) is not None:
        return True
    return False


def require_http_app_access(request: "Request", *, log_admin_token: str | None = None) -> None:
    expected = configured_app_token()
    supplied = (
        request.headers.get("x-conjecta-auth-token", "").strip()
        or _bearer_token(request.headers.get("authorization"))
        or _cookie_token(getattr(request, "cookies", None))
    )
    if supplied and _accept_supplied_token(supplied, log_admin_token=log_admin_token):
        return
    if not expected:
        if _allow_unauthenticated(request.headers, getattr(request, "client", None)):
            return
        from math_agent.web.jwt_auth import phone_auth_enabled

        if phone_auth_enabled():
            raise _http_exception(status_code=401, detail="Authentication required.")
        raise _http_exception(
            status_code=403,
            detail="Conjecta auth token is not configured for a non-local request.",
        )
    raise _http_exception(status_code=401, detail="Invalid Conjecta auth token.")


LOCAL_DEV_USER_ID = "u_local_dev"
LOCAL_DEV_PHONE = "00000000000"


def _extract_access_token(request: "Request") -> str:
    return (
        _bearer_token(request.headers.get("authorization"))
        or _cookie_token(getattr(request, "cookies", None))
        or ""
    )


def require_auth_user(request: "Request"):
    """Return the authenticated phone user for tenant-scoped routes.

    Prefer a valid Conjecta JWT. When phone auth is disabled and the request is
    allowed unauthenticated (loopback / CONJECTA_ALLOW_UNAUTHENTICATED), return
    a stable local-dev sentinel user so stores stay isolated by path.
    """
    from math_agent.web.jwt_auth import AuthUser, phone_auth_enabled
    from math_agent.web.user_ban import ban_message, is_user_banned

    token = _extract_access_token(request)
    if token and _looks_like_jwt(token):
        user = _jwt_user_from_token(token)
        if user is not None:
            if is_user_banned(phone=user.phone, user_id=user.user_id):
                raise _http_exception(status_code=403, detail=ban_message())
            return user
        raise _http_exception(status_code=401, detail="Invalid or expired access token.")

    if phone_auth_enabled():
        raise _http_exception(status_code=401, detail="Authentication required.")

    if _allow_unauthenticated(request.headers, getattr(request, "client", None)):
        return AuthUser(user_id=LOCAL_DEV_USER_ID, phone=LOCAL_DEV_PHONE)

    raise _http_exception(status_code=401, detail="Authentication required.")


def require_admin_user(request: "Request"):
    """Return the authenticated user only when their phone is an admin phone."""
    from math_agent.web.operations import is_admin_phone

    user = require_auth_user(request)
    if not is_admin_phone(getattr(user, "phone", "")):
        raise _http_exception(status_code=403, detail="Administrator access required.")
    return user


def optional_auth_user(request: "Request"):
    """Return AuthUser if JWT present, else None (does not raise)."""
    token = _extract_access_token(request)
    if token and _looks_like_jwt(token):
        return _jwt_user_from_token(token)
    return None


@dataclass
class InMemoryRateLimiter:
    limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
    max_keys: int = MAX_RATE_LIMIT_KEYS
    _hits: "OrderedDict[str, Deque[float]]" = field(default_factory=OrderedDict)

    @classmethod
    def from_env(cls) -> "InMemoryRateLimiter":
        raw = os.getenv("CONJECTA_RATE_LIMIT_PER_MINUTE", "").strip()
        try:
            limit = int(raw) if raw else DEFAULT_RATE_LIMIT_PER_MINUTE
        except ValueError:
            limit = DEFAULT_RATE_LIMIT_PER_MINUTE
        if limit <= 0:
            limit = 0
        return cls(limit_per_minute=limit)

    def _evict(self, window_start: float) -> None:
        """Drop keys whose window is fully expired, then cap the total size.

        The key embeds the request path, which is attacker-controlled: without
        this, varying the path grows ``_hits`` without bound. Expired keys are
        swept from the LRU front; anything still over the cap is evicted
        oldest-first (an evicted attacker key simply restarts its window).
        """
        while self._hits:
            key, hits = next(iter(self._hits.items()))
            if hits and hits[-1] >= window_start:
                break
            del self._hits[key]
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)

    def check(self, key: str, now: float | None = None) -> None:
        if self.limit_per_minute <= 0:
            return
        now = time.monotonic() if now is None else now
        window_start = now - 60.0
        self._evict(window_start)
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
        else:
            self._hits.move_to_end(key)
        while hits and hits[0] < window_start:
            hits.popleft()
        if len(hits) >= self.limit_per_minute:
            raise _http_exception(status_code=429, detail="Rate limit exceeded.")
        hits.append(now)


def request_rate_key(request: Any) -> str:
    host = _client_host(request.headers, getattr(request, "client", None)) or "unknown"
    return f"{host}:{request.url.path}"
