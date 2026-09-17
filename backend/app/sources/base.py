"""Shared plumbing for calling a public data source.

Contract: fetch_json() never raises. Every outcome - success, timeout, 429,
5xx, HTML instead of JSON, JSON that is valid but nonsensical - comes back as a
SourceResult. Callers never need try/except to survive a bad upstream, which is
what keeps one broken source from taking down a whole assessment.
"""
import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app import config


# One retry for errors that are usually momentary (gateway hiccups, dropped
# connections). Deliberately small: these are free services, and the run as a
# whole must stay bounded. 429 and timeouts are never retried.
TRANSIENT_ATTEMPTS = 2
TRANSIENT_STATUSES = {502, 503, 504}
RETRY_BACKOFF_SECONDS = 1.0


class BadPayload(Exception):
    """The source answered, but not with something we can trust."""


class NoMatch(Exception):
    """The source answered correctly, and the answer is 'nothing here'."""


@dataclass
class SourceResult:
    source: str
    request_url: str
    fetched_at: datetime
    duration_ms: int
    ok: bool
    # What the source actually sent (kept even when parsing fails, for audit).
    payload: Any = None
    # Normalised facts extracted from the payload; only set when ok.
    facts: dict[str, Any] = field(default_factory=dict)
    # timeout | rate_limited | http_error | network | bad_payload | no_match
    error_kind: str | None = None
    error_detail: str | None = None
    http_status: int | None = None

    def unavailable_reason(self) -> str:
        return f"{self.source} {self.error_kind}: {self.error_detail}"


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


async def fetch_json(
    client: httpx.AsyncClient,
    source: str,
    url: str,
    params: dict[str, Any],
    parse: Callable[[Any], dict[str, Any]],
) -> SourceResult:
    timeout = config.TIMEOUTS[source]
    request_url = str(httpx.URL(url, params=params))
    fetched_at = datetime.now(timezone.utc)
    started = time.monotonic()

    def result(ok: bool, **kw: Any) -> SourceResult:
        return SourceResult(
            source=source,
            request_url=request_url,
            fetched_at=fetched_at,
            duration_ms=int((time.monotonic() - started) * 1000),
            ok=ok,
            **kw,
        )

    for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
        last_try = attempt == TRANSIENT_ATTEMPTS
        note = f" (after {attempt} attempts)" if attempt > 1 else ""
        try:
            # httpx timeouts are per phase (connect, each read); a server that
            # trickles bytes could exceed them in total. Bound the whole call too.
            async with asyncio.timeout(timeout):
                response = await client.get(url, params=params, timeout=timeout)
        except (TimeoutError, httpx.TimeoutException):
            # Not retried: a second full timeout doubles how long the run takes.
            return result(False, error_kind="timeout", error_detail=f"no response within {timeout:g}s")
        except httpx.HTTPError as exc:
            if not last_try:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                continue
            reason = str(exc) or "connection failed"
            return result(False, error_kind="network", error_detail=f"{type(exc).__name__}: {reason}{note}"[:500])
        if response.status_code in TRANSIENT_STATUSES and not last_try:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
            continue
        break

    status = response.status_code
    if status == 429:
        # Deliberately no retry loop: hammering a free service that just told
        # us to back off is the wrong move. The run is marked partial instead
        # and an analyst can re-run later.
        retry_after = response.headers.get("Retry-After", "unspecified")
        return result(False, http_status=status, error_kind="rate_limited",
                      error_detail=f"HTTP 429, Retry-After: {retry_after}")

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if status >= 400:
        return result(False, http_status=status, payload=payload, error_kind="http_error",
                      error_detail=f"HTTP {status}: {response.text[:200]}")
    if payload is None:
        return result(False, http_status=status, error_kind="bad_payload",
                      error_detail=f"expected JSON, got: {response.text[:200]!r}")

    try:
        facts = parse(payload)
    except NoMatch as exc:
        return result(False, http_status=status, payload=payload, error_kind="no_match", error_detail=str(exc))
    except BadPayload as exc:
        return result(False, http_status=status, payload=payload, error_kind="bad_payload", error_detail=str(exc))
    except Exception as exc:  # a parser bug must not become an outage
        return result(False, http_status=status, payload=payload, error_kind="bad_payload",
                      error_detail=f"could not parse response ({type(exc).__name__}: {exc})"[:500])

    return result(True, http_status=status, payload=payload, facts=facts)
