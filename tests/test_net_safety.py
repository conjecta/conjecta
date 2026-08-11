from __future__ import annotations

import pytest

from math_agent.net_safety import UnsafeFetchURL, validate_public_http_url


@pytest.mark.asyncio
async def test_validate_public_http_url_rejects_non_http():
    with pytest.raises(UnsafeFetchURL, match="http/https"):
        await validate_public_http_url("file:///tmp/source.txt")


@pytest.mark.asyncio
async def test_validate_public_http_url_rejects_loopback():
    with pytest.raises(UnsafeFetchURL, match="private or reserved"):
        await validate_public_http_url("http://127.0.0.1:8000/")
