"""Property-based tests for math_agent.net_safety.is_blocked_ip.

Hypothesis is a hard dev dependency (see pyproject.toml [project.optional-
dependencies].dev); these tests run in CI on every push/PR.
"""
from __future__ import annotations

import ipaddress

from hypothesis import given, settings
from hypothesis import strategies as st

from math_agent.net_safety import is_blocked_ip, normalize_ip

# IPv4 ranges that must always be blocked (kept in sync with the SSRF
# contract: loopback, RFC 1918, link-local).
_BLOCKED_V4_CIDRS = ("127.0.0.0/8", "10.0.0.0/8", "192.168.0.0/16", "169.254.0.0/16")

ipv4_strings = st.integers(min_value=0, max_value=2**32 - 1).map(
    lambda i: str(ipaddress.IPv4Address(i))
)
ipv6_strings = st.integers(min_value=0, max_value=2**128 - 1).map(
    lambda i: str(ipaddress.IPv6Address(i))
)
# Junk: arbitrary text plus strings biased towards IP-ish characters.
junk_strings = st.one_of(
    st.text(),
    st.text(alphabet="0123456789abcdefABCDEF:.[ ]x-f", min_size=0, max_size=64),
)


def _mapped_forms(addr: ipaddress.IPv4Address) -> list[str]:
    """All textual IPv4-mapped IPv6 spellings of *addr*."""
    i = int(addr)
    return [
        f"::ffff:{addr}",
        f"::ffff:{i >> 16:x}:{i & 0xFFFF:x}",
        f"0:0:0:0:0:ffff:{addr}",
        f"0000:0000:0000:0000:0000:ffff:{addr}",
    ]


blocked_ipv4 = st.sampled_from(_BLOCKED_V4_CIDRS).flatmap(
    lambda cidr: st.integers(
        min_value=0, max_value=ipaddress.ip_network(cidr).num_addresses - 1
    ).map(lambda offset, cidr=cidr: ipaddress.ip_network(cidr)[offset])
)


@settings(max_examples=300)
@given(value=st.one_of(ipv4_strings, ipv6_strings, junk_strings))
def test_is_blocked_ip_never_raises_and_returns_bool(value: str):
    result = is_blocked_ip(value)
    assert isinstance(result, bool)


@settings(max_examples=200)
@given(addr=blocked_ipv4)
def test_every_mapped_form_of_a_blocked_ipv4_is_blocked(addr: ipaddress.IPv4Address):
    assert is_blocked_ip(str(addr))  # sanity: the plain form is blocked
    for form in _mapped_forms(addr):
        assert is_blocked_ip(form), f"mapped form {form} of {addr} not blocked"


@settings(max_examples=200)
@given(addr=blocked_ipv4)
def test_mapped_forms_of_blocked_ipv4_never_false_negative(addr: ipaddress.IPv4Address):
    """normalize_ip must collapse mapped forms before the range check."""
    for form in _mapped_forms(addr):
        normalized = normalize_ip(form)
        assert isinstance(normalized, ipaddress.IPv4Address)
        assert normalized == addr
