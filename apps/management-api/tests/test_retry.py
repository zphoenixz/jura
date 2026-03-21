import pytest
import httpx
import respx

from app.core.http_client import resilient_request


@pytest.mark.asyncio
async def test_retry_on_429():
    with respx.mock:
        route = respx.get("https://api.example.com/test")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
        result = await resilient_request("GET", "https://api.example.com/test")
        assert result.status_code == 200
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_retry_on_connection_error():
    with respx.mock:
        route = respx.get("https://api.example.com/test")
        route.side_effect = [
            httpx.ConnectError("connection refused"),
            httpx.Response(200, json={"ok": True}),
        ]
        result = await resilient_request("GET", "https://api.example.com/test")
        assert result.status_code == 200


@pytest.mark.asyncio
async def test_gives_up_after_max_retries():
    with respx.mock:
        route = respx.get("https://api.example.com/test")
        route.side_effect = [httpx.Response(429)] * 6
        with pytest.raises(httpx.HTTPStatusError):
            await resilient_request("GET", "https://api.example.com/test", max_retries=5)
