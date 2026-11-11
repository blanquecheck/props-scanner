"""GET-only HTTP client for venue market data.

Hard rule: this project never places orders. This client only exposes `get`;
there is no post/put/delete method, and it never attaches auth headers.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class ReadOnlyClient:
    def __init__(self, base_url: str, min_interval: float = 0.08, timeout: float = 20.0,
                 max_retries: int = 4):
        self._client = httpx.Client(base_url=base_url, timeout=timeout,
                                    headers={"User-Agent": "props-scanner/0.1 (read-only)"})
        self._min_interval = min_interval
        self._last = 0.0
        self._max_retries = max_retries

    def get(self, path: str, params: dict | None = None):
        attempt = 0
        while True:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self._client.get(path, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                attempt += 1
                if not retryable or attempt > self._max_retries:
                    raise
                backoff = min(2 ** attempt, 20)
                log.warning("GET %s failed (%s); retry %d in %ss", path, status or e, attempt, backoff)
                time.sleep(backoff)

    def close(self) -> None:
        self._client.close()
