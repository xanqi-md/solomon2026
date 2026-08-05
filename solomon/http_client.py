from __future__ import annotations

import asyncio
import importlib.util
import time
from typing import Any, Self

import httpx


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# h2 が入っていれば HTTP/2 を使う。無ければ HTTP/1.1 へ自動フォールバック
HTTP2_AVAILABLE = importlib.util.find_spec("h2") is not None

# brotli を解凍できる場合のみ br を広告する
_ACCEPT_ENCODING = "gzip, deflate"
if importlib.util.find_spec("brotli") or importlib.util.find_spec("brotlicffi"):
    _ACCEPT_ENCODING += ", br"

BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": _ACCEPT_ENCODING,
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Connection": "keep-alive",
}


class TTLCache:
    def __init__(self, ttl: float = 300.0) -> None:
        self._ttl = ttl
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            hit = self._data.get(key)
            if hit and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
            self._data.pop(key, None)
            return None

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data[key] = (time.monotonic(), value)


class HttpClient:
    """礼儀正しいクライアント: 同時接続を絞り、指数バックオフで再試行する。"""

    def __init__(
        self,
        *,
        concurrency: int = 4,
        timeout: float = 20.0,
        retries: int = 3,
        cache_ttl: float = 300.0,
    ) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._retries = retries
        self._cache = TTLCache(cache_ttl)
        self._client = httpx.AsyncClient(
            headers=BROWSER_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            http2=HTTP2_AVAILABLE,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    from typing import Self


    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def get_text(self, url: str, **kwargs) -> str:
        cached = await self._cache.get(url)
        if cached is not None:
            return cached
        resp = await self._request(url, **kwargs)
        await self._cache.set(url, resp.text)
        return resp.text

    async def get_json(self, url: str, **kwargs) -> Any:
        cached = await self._cache.get(url)
        if cached is not None:
            return cached
        resp = await self._request(url, **kwargs)
        data = resp.json()
        await self._cache.set(url, data)
        return data

    async def _request(self, url: str, **kwargs) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self._retries):
            async with self._sem:
                try:
                    resp = await self._client.get(url, **kwargs)
                except httpx.HTTPError as exc:
                    last = exc
                else:
                    if resp.status_code < 400:
                        return resp
                    last = httpx.HTTPStatusError(
                        f"{resp.status_code} for {url}",
                        request=resp.request,
                        response=resp,
                    )
                    # 400/404 はリトライしても無駄
                    if resp.status_code in (400, 404):
                        raise last
            await asyncio.sleep(0.8 * (2**attempt))
        assert last is not None
        raise last
