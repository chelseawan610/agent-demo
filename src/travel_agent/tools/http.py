import json
import os
import socket
import time
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


USER_AGENT = (
    "chelseawan610-agent-demo/0.1 "
    "(https://github.com/chelseawan610/agent-demo)"
)


class ExternalToolError(RuntimeError):
    """A normalized public-API failure safe to expose in tool status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool | None = None,
        status_code: int | None = None,
        attempts: int | None = None,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.attempts = attempts
        self.retry_after_seconds = retry_after_seconds


class JsonHttpClient:
    """Small JSON client with explicit retry, cache, and throttle policies."""

    def __init__(
        self,
        timeout_seconds: float | None = None,
        attempts: int = 3,
        cache_ttl_seconds: float | None = None,
        backoff_base_seconds: float | None = None,
        max_backoff_seconds: float | None = None,
    ):
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("TRAVEL_API_TIMEOUT_SECONDS", "20")
        )
        self.attempts = max(1, attempts)
        self.cache_ttl_seconds = (
            cache_ttl_seconds
            if cache_ttl_seconds is not None
            else float(os.getenv("TRAVEL_API_CACHE_TTL_SECONDS", "300"))
        )
        self.backoff_base_seconds = (
            backoff_base_seconds
            if backoff_base_seconds is not None
            else float(os.getenv("TRAVEL_API_BACKOFF_SECONDS", "1"))
        )
        self.max_backoff_seconds = (
            max_backoff_seconds
            if max_backoff_seconds is not None
            else float(os.getenv("TRAVEL_API_MAX_BACKOFF_SECONDS", "8"))
        )
        self._cache: dict[str, Any] = {}
        self._cache_time: dict[str, float] = {}
        self._last_request: dict[str, float] = {}
        self._lock = Lock()

    def get_json(
        self,
        url: str,
        params: dict[str, object] | None = None,
        *,
        min_interval_seconds: float = 0.0,
        cache_ttl_seconds: float | None = None,
    ) -> Any:
        query = urlencode(params or {}, doseq=True)
        request_url = f"{url}?{query}" if query else url
        ttl = self.cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
        with self._lock:
            cached_at = self._cache_time.get(request_url)
            if (
                ttl > 0
                and request_url in self._cache
                and cached_at is not None
                and time.monotonic() - cached_at <= ttl
            ):
                return self._cache[request_url]
            self._cache.pop(request_url, None)
            self._cache_time.pop(request_url, None)

        host = request_url.split("/", 3)[2]
        last_error: ExternalToolError | None = None
        for attempt in range(1, self.attempts + 1):
            self._throttle(host, min_interval_seconds)
            request = Request(
                request_url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.load(response)
                with self._lock:
                    self._cache[request_url] = payload
                    self._cache_time[request_url] = time.monotonic()
                return payload
            except HTTPError as error:
                retryable = error.code in {408, 425, 429} or 500 <= error.code < 600
                retry_after = self._retry_after(error)
                last_error = ExternalToolError(
                    f"http_{error.code}",
                    str(error),
                    retryable=retryable,
                    status_code=error.code,
                    attempts=attempt,
                    retry_after_seconds=retry_after,
                )
                if not retryable:
                    break
            except (TimeoutError, socket.timeout) as error:
                last_error = ExternalToolError(
                    "timeout",
                    str(error) or "request timed out",
                    retryable=True,
                    attempts=attempt,
                )
            except URLError as error:
                last_error = ExternalToolError(
                    "connection_error",
                    str(error.reason),
                    retryable=True,
                    attempts=attempt,
                )
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                last_error = ExternalToolError(
                    "invalid_json",
                    str(error),
                    retryable=False,
                    attempts=attempt,
                )
                break

            if attempt < self.attempts:
                delay = min(
                    self.max_backoff_seconds,
                    self.backoff_base_seconds * (2 ** (attempt - 1)),
                )
                if last_error and last_error.retry_after_seconds is not None:
                    delay = min(
                        self.max_backoff_seconds,
                        max(delay, last_error.retry_after_seconds),
                    )
                time.sleep(delay)

        raise last_error or ExternalToolError("unknown_error", "public API request failed")

    @staticmethod
    def _retry_after(error: HTTPError) -> float | None:
        """Read a bounded Retry-After header without trusting bad values."""

        raw = error.headers.get("Retry-After") if error.headers else None
        try:
            value = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
        return max(0.0, min(value, 60.0)) if value is not None else None

    def _throttle(self, host: str, min_interval_seconds: float) -> None:
        if min_interval_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            remaining = min_interval_seconds - (now - self._last_request.get(host, 0.0))
            if remaining > 0:
                time.sleep(remaining)
            self._last_request[host] = time.monotonic()


HTTP_CLIENT = JsonHttpClient()
