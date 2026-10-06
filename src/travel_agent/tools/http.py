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

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class JsonHttpClient:
    """Small JSON client with cache, retries, timeouts, and host throttling."""

    def __init__(self, timeout_seconds: float | None = None, attempts: int = 3):
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("TRAVEL_API_TIMEOUT_SECONDS", "20")
        )
        self.attempts = attempts
        self._cache: dict[str, Any] = {}
        self._last_request: dict[str, float] = {}
        self._lock = Lock()

    def get_json(
        self,
        url: str,
        params: dict[str, object] | None = None,
        *,
        min_interval_seconds: float = 0.0,
    ) -> Any:
        query = urlencode(params or {}, doseq=True)
        request_url = f"{url}?{query}" if query else url
        with self._lock:
            if request_url in self._cache:
                return self._cache[request_url]

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
                return payload
            except HTTPError as error:
                last_error = ExternalToolError(f"http_{error.code}", str(error))
                if error.code != 429 and not 500 <= error.code < 600:
                    break
            except (TimeoutError, socket.timeout) as error:
                last_error = ExternalToolError("timeout", str(error) or "request timed out")
            except URLError as error:
                last_error = ExternalToolError("connection_error", str(error.reason))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                last_error = ExternalToolError("invalid_json", str(error))
                break

            if attempt < self.attempts:
                time.sleep(float(attempt))

        raise last_error or ExternalToolError("unknown_error", "public API request failed")

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
