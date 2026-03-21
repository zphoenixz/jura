import asyncio

import httpx

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=120.0)
    return _client


async def resilient_request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json: dict | None = None,
    params: dict | None = None,
    max_retries: int = 5,
) -> httpx.Response:
    client = get_client()
    for attempt in range(max_retries + 1):
        try:
            response = await client.request(
                method, url, headers=headers, json=json, params=params
            )
            if response.status_code == 429:
                if attempt >= max_retries:
                    response.raise_for_status()
                wait = int(response.headers.get("Retry-After", "0"))
                wait = max(wait, min(2 ** attempt, 30))
                await asyncio.sleep(wait)
                continue
            return response
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            if isinstance(exc, httpx.ConnectError):
                # Reset client on DNS/connection failures to avoid stale state
                global _client
                if _client:
                    await _client.aclose()
                    _client = None
                client = get_client()
            if attempt >= max_retries:
                raise
            wait = min(2 ** attempt, 30)
            await asyncio.sleep(wait)
    raise httpx.HTTPError(f"Failed after {max_retries} retries")
