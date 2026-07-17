#!/usr/bin/env python3
"""Strict, source-responsive HTTP client shared by source collectors.

The request ledger and circuit breaker live outside the repository so separate
scripts and panel restarts share pacing and real source-failure state.  The
ledger is telemetry, not a locally invented daily access ceiling.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import email.message
import fcntl
import json
import os
import pathlib
import random
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable

from source_policy import local_state_root, require_local_collector


@dataclass(frozen=True)
class ProviderPolicy:
    name: str
    daily_budget: int | None
    min_interval: float
    circuit_failures: int = 3
    circuit_seconds: int = 300


POLICIES = {
    # Source access is paced, and real failures open a short circuit.  Do not
    # impose a made-up request total: an operator may continue whenever the
    # source is responding normally.
    "chess-results": ProviderPolicy("chess-results", None, 1.0),
    "fide": ProviderPolicy("fide", None, 2.0),
    "lichess": ProviderPolicy("lichess", None, 0.2),
    "other": ProviderPolicy("other", None, 0.5),
}


class SourceHTTPError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def provider_for_url(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "chess-results.com" or host.endswith(".chess-results.com"):
        return "chess-results"
    if host == "ratings.fide.com" or host.endswith(".fide.com"):
        return "fide"
    if host == "lichess.org" or host.endswith(".lichess.org"):
        return "lichess"
    return "other"


def tls_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore[import-not-found]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _atomic_json(path: pathlib.Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    os.chmod(path, 0o600)


@contextlib.contextmanager
def _locked_ledger() -> Iterable[tuple[pathlib.Path, dict]]:
    root = local_state_root() / "network"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(local_state_root(), 0o700)
    os.chmod(root, 0o700)
    lock_path = root / "quota.lock"
    ledger_path = root / "quota.json"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                ledger = {"schemaVersion": 1, "providers": {}}
            yield ledger_path, ledger
            _atomic_json(ledger_path, ledger)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _reserve_request(provider: str) -> None:
    policy = POLICIES[provider]
    today = dt.date.today().isoformat()
    now = time.time()
    with _locked_ledger() as (_path, ledger):
        providers = ledger.setdefault("providers", {})
        state = providers.setdefault(provider, {})
        if state.get("date") != today:
            state.clear()
            state.update(date=today, requests=0, consecutiveFailures=0, lastRequestAt=0.0, circuitOpenUntil=0.0)
        if float(state.get("circuitOpenUntil") or 0) > now:
            remaining = int(float(state["circuitOpenUntil"]) - now)
            raise SourceHTTPError(
                "SOURCE_CIRCUIT_OPEN",
                f"{provider} 连续失败后已熔断，请在约 {remaining} 秒后重试。",
            )
        requests = int(state.get("requests") or 0)
        if policy.daily_budget is not None and requests >= policy.daily_budget:
            raise SourceHTTPError(
                "VISIT_BUDGET_EXHAUSTED",
                f"{provider} 今日访问预算 {policy.daily_budget} 已用完。",
            )
        wait = policy.min_interval - (now - float(state.get("lastRequestAt") or 0))
        if wait > 0:
            time.sleep(wait)
            now = time.time()
        state["requests"] = requests + 1
        state["lastRequestAt"] = now


def _record_result(provider: str, success: bool, *, force_circuit: bool = False) -> None:
    policy = POLICIES[provider]
    with _locked_ledger() as (_path, ledger):
        state = ledger.setdefault("providers", {}).setdefault(provider, {})
        if success:
            state["consecutiveFailures"] = 0
            state["lastSuccessAt"] = time.time()
        else:
            failures = int(state.get("consecutiveFailures") or 0) + 1
            if force_circuit:
                failures = max(failures, policy.circuit_failures)
            state["consecutiveFailures"] = failures
            state["lastFailureAt"] = time.time()
            if failures >= policy.circuit_failures:
                state["circuitOpenUntil"] = time.time() + policy.circuit_seconds


def _should_force_circuit(error: Exception | None) -> bool:
    """Open immediately only for an explicit block/rate-limit response.

    A logical page request has its own retry loop.  Counting every internal
    retry as an independent outage meant one transient failure exhausted all
    three circuit slots and stopped the entire batch.  Timeouts and temporary
    transport errors now consume one slot only after that request is exhausted.
    """
    if isinstance(error, SourceHTTPError):
        return error.code == "SOURCE_BLOCKED_OR_RATE_LIMITED"
    return isinstance(error, urllib.error.HTTPError) and error.code in {403, 429}


def reserve_provider_request(provider: str) -> None:
    """Public streaming hook for collectors with source-specific validators."""
    if provider not in POLICIES:
        provider = "other"
    require_local_collector(provider)
    _reserve_request(provider)


def record_provider_result(provider: str, success: bool) -> None:
    if provider not in POLICIES:
        provider = "other"
    _record_result(provider, success)


class _ManagedResponse:
    """Proxy a streaming response and account for its complete lifecycle."""

    def __init__(self, response, provider: str):
        self._response = response
        self._provider = provider
        self._finalized = False

    def _finish(self, success: bool) -> None:
        if not self._finalized:
            self._finalized = True
            _record_result(self._provider, success)

    def __getattr__(self, name: str):
        return getattr(self._response, name)

    def __enter__(self):
        self._response.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        success = exc_type is None
        try:
            return self._response.__exit__(exc_type, exc_value, traceback)
        except BaseException:
            success = False
            raise
        finally:
            self._finish(success)

    def read(self, *args, **kwargs):
        try:
            return self._response.read(*args, **kwargs)
        except BaseException:
            self._finish(False)
            raise

    def close(self) -> None:
        try:
            self._response.close()
        except BaseException:
            self._finish(False)
            raise
        else:
            self._finish(True)


def _content_type(headers: email.message.Message) -> str:
    return (headers.get_content_type() or "").lower()


def _looks_blocked(body: bytes) -> bool:
    sample = body[:20000].lower()
    markers = (
        b"access denied",
        b"too many requests",
        b"temporarily blocked",
        b"request has been blocked",
        b"captcha",
        b"cloudflare ray id",
    )
    return any(marker in sample for marker in markers)


def fetch_bytes(
    request_or_url: urllib.request.Request | str,
    *,
    timeout: float = 60,
    retries: int = 2,
    expected_types: tuple[str, ...] = (),
    validator: Callable[[bytes, email.message.Message], None] | None = None,
) -> tuple[bytes, str, email.message.Message]:
    request = request_or_url if isinstance(request_or_url, urllib.request.Request) else urllib.request.Request(request_or_url)
    url = request.full_url
    provider = provider_for_url(url)
    require_local_collector(provider)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _reserve_request(provider)
            with urllib.request.urlopen(request, timeout=timeout, context=tls_context()) as response:
                body = response.read()
                final_url = response.geturl()
                headers = response.headers
            if not body:
                raise SourceHTTPError("SOURCE_EMPTY_RESPONSE", f"{provider} 返回空响应：{url}")
            ctype = _content_type(headers)
            if expected_types and not any(ctype.startswith(value) for value in expected_types):
                raise SourceHTTPError(
                    "SOURCE_UNEXPECTED_CONTENT_TYPE",
                    f"{provider} 返回 {ctype or 'unknown'}，预期 {expected_types}。",
                )
            if _looks_blocked(body):
                raise SourceHTTPError("SOURCE_BLOCKED_OR_RATE_LIMITED", f"{provider} 返回拦截或限流页面。")
            if validator:
                validator(body, headers)
            _record_result(provider, True)
            return body, final_url, headers
        except SourceHTTPError as error:
            last_error = error
            if error.code in {"VISIT_BUDGET_EXHAUSTED", "SOURCE_CIRCUIT_OPEN"}:
                raise
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as error:
            last_error = error
        if attempt < retries:
            time.sleep(min(12.0, (2**attempt) + random.uniform(0.2, 0.9)))
    _record_result(provider, False, force_circuit=_should_force_circuit(last_error))
    if isinstance(last_error, SourceHTTPError):
        raise last_error
    raise SourceHTTPError("SOURCE_NETWORK_FAILURE", f"{provider} 请求失败：{last_error}") from last_error


def open_response(
    request: urllib.request.Request,
    *,
    timeout: float = 90,
    retries: int = 2,
):
    """Open a streaming response under the shared local quota/circuit policy.

    Legacy collectors that stream directly can use this adapter while still
    sharing the same acknowledgement, TLS, rate limit and breaker state. The
    request is only recorded as successful after the response lifecycle ends.
    Source-specific callers remain responsible for validating the body.
    """
    provider = provider_for_url(request.full_url)
    require_local_collector(provider)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _reserve_request(provider)
            response = urllib.request.urlopen(request, timeout=timeout, context=tls_context())
            return _ManagedResponse(response, provider)
        except SourceHTTPError as error:
            last_error = error
            if error.code in {"VISIT_BUDGET_EXHAUSTED", "SOURCE_CIRCUIT_OPEN"}:
                raise
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as error:
            last_error = error
        if attempt < retries:
            time.sleep(min(12.0, (2**attempt) + random.uniform(0.2, 0.9)))
    _record_result(provider, False, force_circuit=_should_force_circuit(last_error))
    if isinstance(last_error, SourceHTTPError):
        raise last_error
    raise SourceHTTPError("SOURCE_NETWORK_FAILURE", f"{provider} 流式请求失败：{last_error}") from last_error


def download_to_path(
    request_or_url: urllib.request.Request | str,
    target: pathlib.Path,
    *,
    timeout: float = 180,
    retries: int = 2,
    expected_size: int = 0,
    minimum_ratio: float = 0.5,
    magic: bytes = b"",
) -> dict[str, int | str]:
    """Stream a large response to a unique temp file and atomically promote it."""
    request = request_or_url if isinstance(request_or_url, urllib.request.Request) else urllib.request.Request(request_or_url)
    provider = provider_for_url(request.full_url)
    require_local_collector(provider)
    target.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.download-", dir=target.parent)
        os.close(fd)
        tmp = pathlib.Path(name)
        try:
            _reserve_request(provider)
            with urllib.request.urlopen(request, timeout=timeout, context=tls_context()) as response, tmp.open("wb") as handle:
                content_type = _content_type(response.headers)
                if content_type.startswith("text/html"):
                    raise SourceHTTPError("SOURCE_UNEXPECTED_CONTENT_TYPE", f"{provider} 大文件下载返回 HTML。")
                announced = int(response.headers.get("Content-Length") or 0)
                written = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
            if announced and announced != written:
                raise SourceHTTPError("SOURCE_TRUNCATED_DOWNLOAD", f"下载不完整：{written}/{announced} 字节。")
            if expected_size and written < int(expected_size * minimum_ratio):
                raise SourceHTTPError(
                    "SOURCE_TRUNCATED_DOWNLOAD",
                    f"下载文件相对目录元数据异常小：{written}/{expected_size} 字节。",
                )
            if magic and tmp.open("rb").read(len(magic)) != magic:
                raise SourceHTTPError("SOURCE_FILE_SIGNATURE_INVALID", f"{target.name} 文件签名无效。")
            os.replace(tmp, target)
            _record_result(provider, True)
            return {"bytes": written, "contentType": content_type}
        except SourceHTTPError as error:
            last_error = error
            if error.code in {"VISIT_BUDGET_EXHAUSTED", "SOURCE_CIRCUIT_OPEN"}:
                tmp.unlink(missing_ok=True)
                raise
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as error:
            last_error = error
        finally:
            tmp.unlink(missing_ok=True)
        if attempt < retries:
            time.sleep(min(12.0, (2**attempt) + random.uniform(0.2, 0.9)))
    _record_result(provider, False, force_circuit=_should_force_circuit(last_error))
    if isinstance(last_error, SourceHTTPError):
        raise last_error
    raise SourceHTTPError("SOURCE_NETWORK_FAILURE", f"{provider} 大文件下载失败：{last_error}") from last_error
