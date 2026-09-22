"""Client for the public TED (Tenders Electronic Daily) Search API.

The Search API is anonymous for published notices: no key, no account.
Reference: https://docs.ted.europa.eu/api/latest/search.html

The call is a POST to https://api.ted.europa.eu/v3/notices/search; a GET on the
same URL answers HTTP 405, so never build the request as a GET.

Privacy rule (constitution, section 4 / LGPD): the request asks the API for an
ALLOW LIST of eight public, non-personal fields, and `src/matching.py` applies
the same allow list again on the way out. Personal fields of the source
(buyer-person, winner-person, winner-touchpoint-name, jury-member-name-lot,
participant-name-lot, first-name-ubo and contact points) are never requested
and never copied.

Rate limits published by TED: 700 requests in the last minute and 3 concurrent
downloads (https://ted.europa.eu/en/simap/developers-corner-for-reusers). This
client makes one request at a time (1 of the 3 allowed downloads) and waits
between pages, which keeps it an order of magnitude under 700 per minute.

Failures are retried with a growing wait, and a `Retry-After` header sent by
TED is obeyed. Only transient failures are retried; a 400 is a bug in our
query and must surface at once.

Everything here takes an injectable `transport` - a function that receives
(url, json_body) and returns a dict - so tests run without any network.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence

try:  # running inside the Actor image (python src/main.py)
    from matching import dedupe, matches, normalize_notice
except ImportError:  # running as a package (python -m src.main)
    from .matching import dedupe, matches, normalize_notice

TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"

# The eight fields we are allowed to ask for and to publish.
# https://docs.ted.europa.eu/ODS/latest/reuse/field-list.html
ALLOWED_API_FIELDS: tuple = (
    "notice-title",
    "buyer-name",
    "buyer-country",
    "classification-cpv",
    "total-value",
    "deadline-receipt-tender-date-lot",
    "publication-number",
    "announcement-url",
)

# TED rejects a page size above 250.
MAX_PAGE_SIZE = 250
DEFAULT_PAGE_SIZE = 100

# Limits TED publishes for reusers:
# https://ted.europa.eu/en/simap/developers-corner-for-reusers
TED_MAX_REQUESTS_PER_MINUTE = 700
TED_MAX_CONCURRENT_DOWNLOADS = 3

# One request at a time (1 of the 3 downloads TED allows at once), with a pause
# between pages. 700 requests per minute is the published ceiling, i.e. one
# request every 0.0857 s; 0.25 s between pages keeps us an order of magnitude
# below it and is polite to a free public service.
DEFAULT_DELAY_SECONDS = 0.25

# Retry policy for transient failures.
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0

# HTTP answers worth trying again: rate limiting, overload, gateway trouble.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Safety net so a broken "total" in a response can never spin forever.
DEFAULT_MAX_PAGES = 100

DEFAULT_TIMEOUT_SECONDS = 30.0

# Sent on every call so TED can see who is calling, as the reuser page asks.
USER_AGENT = "ted-tender-matcher (Apify Actor; +https://apify.com)"

# Keys a TED answer may use for the list of notices and for the total count.
_NOTICE_LIST_KEYS = ("notices", "results", "items", "data")
_TOTAL_KEYS = ("totalNoticeCount", "totalCount", "total", "count")


class TedApiError(RuntimeError):
    """The TED API answered something we cannot use."""


class TedHttpError(TedApiError):
    """TED answered with an HTTP error status."""

    def __init__(self, status_code: int, message: str = "", retry_after: Optional[float] = None):
        super().__init__(message or ("TED answered HTTP %s" % status_code))
        self.status_code = int(status_code)
        self.retry_after = retry_after


class TedNetworkError(TedApiError):
    """The call to TED never produced an answer (timeout, DNS, reset)."""


def is_retryable(exc: BaseException) -> bool:
    """True when trying the very same request again can reasonably work.

    A malformed query (400) or a non-JSON body is our bug: it fails at once,
    so it shows up in the log instead of being retried four times.
    """
    if isinstance(exc, TedHttpError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    return isinstance(exc, TedNetworkError)


def _backoff_delay(attempt: int, backoff_seconds: float, retry_after: Optional[float]) -> float:
    """Wait before attempt number `attempt` + 1, doubling each time.

    A `Retry-After` sent by TED wins when it asks for a longer wait; the whole
    thing is capped so a bogus header cannot park the run for hours.
    """
    delay = float(backoff_seconds) * (2 ** max(0, attempt - 1))
    if retry_after is not None:
        try:
            delay = max(delay, float(retry_after))
        except (TypeError, ValueError):
            pass
    return max(0.0, min(delay, MAX_BACKOFF_SECONDS))


def retrying_transport(
    inner: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    log: Optional[Callable[[str], None]] = None,
) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    """Wrap a transport so transient failures are retried with a growing wait.

    The last failure is re-raised unchanged, so the caller still sees the real
    reason instead of a generic one.
    """
    max_attempts = max(1, int(max_attempts))

    def transport(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        attempt = 1
        while True:
            try:
                return inner(url, payload)
            except Exception as exc:  # noqa: BLE001 - re-raised below when final
                if attempt >= max_attempts or not is_retryable(exc):
                    raise
                delay = _backoff_delay(
                    attempt,
                    backoff_seconds,
                    getattr(exc, "retry_after", None),
                )
                if log:
                    log(
                        "TED call failed (%s); attempt %d of %d, waiting %.1f s."
                        % (exc, attempt, max_attempts, delay)
                    )
                if delay > 0:
                    sleep(delay)
                attempt += 1

    return transport


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------
def build_search_body(
    query: str = "*",
    page: int = 1,
    limit: int = DEFAULT_PAGE_SIZE,
    fields: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Build the JSON body of one POST to the TED search endpoint.

    `fields` is filtered against ALLOWED_API_FIELDS, so a caller cannot widen
    the request into personal data even by mistake.
    """
    if fields is None:
        wanted = list(ALLOWED_API_FIELDS)
    else:
        wanted = [f for f in fields if f in ALLOWED_API_FIELDS]
        if not wanted:
            wanted = list(ALLOWED_API_FIELDS)

    page = max(1, int(page))
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))

    return {
        "query": str(query or "*").strip() or "*",
        "fields": wanted,
        "page": page,
        "limit": limit,
    }


def build_expert_query(
    cpv_codes: Optional[Sequence[str]] = None,
    countries: Optional[Sequence[str]] = None,
    since_date: Optional[str] = None,
) -> str:
    """Build a TED expert query string from the coarse filters.

    Only filters TED understands well go into the query; the fine grained
    filtering (value, keywords, CPV hierarchy) is done locally by
    `src/matching.py`, so a change in the query language cannot silently widen
    the output.
    """
    clauses: List[str] = []

    codes = [str(c).strip().split("-")[0] for c in (cpv_codes or []) if str(c or "").strip()]
    if codes:
        clauses.append("(" + " OR ".join("classification-cpv=%s" % c for c in codes) + ")")

    places = [str(c).strip().upper() for c in (countries or []) if str(c or "").strip()]
    if places:
        clauses.append("(" + " OR ".join("buyer-country=%s" % c for c in places) + ")")

    if since_date:
        clauses.append("publication-date>=%s" % str(since_date).strip())

    return " AND ".join(clauses) if clauses else "*"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
def requests_transport(
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    """Return a transport backed by `requests`, with a timeout on every call.

    Imported lazily so the pure functions of this module (and their tests) do
    not need `requests` installed.
    """
    import requests  # noqa: PLC0415 - lazy on purpose

    def transport(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=timeout_seconds,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
        except requests.RequestException as exc:  # timeout, DNS, reset
            raise TedNetworkError("call to TED failed: %s" % exc) from exc

        if response.status_code >= 400:
            raise TedHttpError(
                response.status_code,
                "TED answered HTTP %s for %s" % (response.status_code, url),
                retry_after=_parse_retry_after(response.headers.get("Retry-After")),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise TedApiError("TED answered a body that is not JSON: %s" % exc) from exc

    return transport


def _parse_retry_after(value: Any) -> Optional[float]:
    """Read a numeric `Retry-After` header. A date form is ignored on purpose."""
    if value is None:
        return None
    try:
        return max(0.0, float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _extract_notices(answer: Any) -> List[dict]:
    if not isinstance(answer, dict):
        raise TedApiError("TED answered %s, expected a JSON object" % type(answer).__name__)
    for key in _NOTICE_LIST_KEYS:
        value = answer.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_total(answer: Dict[str, Any]) -> Optional[int]:
    for key in _TOTAL_KEYS:
        value = answer.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------
def fetch_notices(
    query: str = "*",
    max_results: int = 100,
    page_size: int = DEFAULT_PAGE_SIZE,
    fields: Optional[Sequence[str]] = None,
    transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    max_pages: int = DEFAULT_MAX_PAGES,
    log: Optional[Callable[[str], None]] = None,
    url: str = TED_SEARCH_URL,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> List[dict]:
    """Page through the TED search endpoint and return the raw notice dicts.

    Stops at the first of: `max_results` collected, an empty page, the total
    reported by TED, or `max_pages`. Waits `delay_seconds` between pages to
    stay well inside the published rate limit, and retries a transient failure
    up to `max_attempts` times with a growing wait.
    """
    if transport is None:
        transport = requests_transport()
    transport = retrying_transport(
        transport,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        sleep=sleep,
        log=log,
    )

    max_results = max(0, int(max_results))
    if max_results == 0:
        return []
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))

    collected: List[dict] = []
    page = 1
    while page <= max(1, int(max_pages)):
        remaining = max_results - len(collected)
        if remaining <= 0:
            break
        body = build_search_body(
            query=query,
            page=page,
            limit=min(page_size, remaining),
            fields=fields,
        )
        answer = transport(url, body)
        batch = _extract_notices(answer)
        if log:
            log("TED page %d returned %d notices" % (page, len(batch)))
        if not batch:
            break
        collected.extend(batch)

        total = _extract_total(answer)
        if total is not None and len(collected) >= total:
            break
        if len(batch) < body["limit"]:
            break
        if len(collected) >= max_results:
            break

        page += 1
        if delay_seconds > 0:
            sleep(delay_seconds)

    return collected[:max_results]


# ---------------------------------------------------------------------------
# Whole pipeline, with no Apify dependency so tests can run it
# ---------------------------------------------------------------------------
def collect_matching_notices(
    criteria: Optional[dict] = None,
    max_results: int = 100,
    transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    since_date: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    stats: Optional[Dict[str, int]] = None,
) -> List[dict]:
    """Fetch, normalize, filter and dedupe: the eight public fields only.

    `criteria` accepts the keys `src/matching.py` understands: cpv_codes,
    countries, min_value_eur and keywords.

    `stats`, when given, is filled with the counts of this call: how many raw
    notices TED answered (`noticesRead`), how many survived the filters
    (`matchedNotices`) and how many repeats were dropped
    (`duplicatesDropped`). The caller needs those numbers to tell the buyer, in
    the dataset itself, that a run with zero matches did read the notices.
    """
    criteria = criteria or {}
    query = build_expert_query(
        cpv_codes=criteria.get("cpv_codes"),
        countries=criteria.get("countries"),
        since_date=since_date,
    )
    # Fetch wider than the result cap: the local filters (value, keywords, CPV
    # hierarchy) drop part of every page.
    fetch_cap = max(max_results, min(max_results * 5, 2000))
    raw = fetch_notices(
        query=query,
        max_results=fetch_cap,
        page_size=page_size,
        transport=transport,
        delay_seconds=delay_seconds,
        sleep=sleep,
        max_pages=max_pages,
        log=log,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
    normalized = [normalize_notice(item) for item in raw]
    kept = [item for item in normalized if matches(item, criteria)]
    unique = dedupe(kept)
    result = unique[:max_results]
    if stats is not None:
        stats["noticesRead"] = len(raw)
        stats["matchedNotices"] = len(result)
        stats["duplicatesDropped"] = len(kept) - len(unique)
    return result
