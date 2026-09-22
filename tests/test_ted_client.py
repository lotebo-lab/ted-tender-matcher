"""Hand-rolled test runner for src/ted_client.py.

pytest is not installed in .venv and pip has no network inside the sandbox, so
this file runs its own cases and prints "N/N passed" on the last line, exiting
with code 1 when any case fails.

NO NETWORK IS USED. Every case injects a fake `transport` that returns an
invented TED answer, so no call to api.ted.europa.eu is ever made.

The invented answers deliberately include personal fields that the real source
publishes (buyer-person, winner-person, winner-touchpoint-name,
jury-member-name-lot, participant-name-lot, first-name-ubo, contact points,
e-mails and phones). The decisive cases prove that none of them survives into
the Actor output.

Run with:
    ../../.venv/bin/python tests/test_ted_client.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from matching import OUTPUT_KEYS  # noqa: E402
from ted_client import (  # noqa: E402
    ALLOWED_API_FIELDS,
    DEFAULT_DELAY_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    MAX_BACKOFF_SECONDS,
    MAX_PAGE_SIZE,
    TED_MAX_CONCURRENT_DOWNLOADS,
    TED_MAX_REQUESTS_PER_MINUTE,
    TED_SEARCH_URL,
    TedApiError,
    TedHttpError,
    TedNetworkError,
    _parse_retry_after,
    build_expert_query,
    build_search_body,
    collect_matching_notices,
    fetch_notices,
    is_retryable,
    retrying_transport,
)

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# ---------------------------------------------------------------------------
# Invented TED records. Public fields plus every personal field we must drop.
# ---------------------------------------------------------------------------
PERSONAL_VALUES = [
    "Marie Dupont",
    "Joao Silva",
    "Jean Bernard",
    "Ana Costa",
    "Luis Pereira",
    "Helena",
    "Pierre Martin",
    "pierre.martin@lyon.fr",
    "+33 4 72 00 00 00",
    "marches@lyon.fr",
]

PERSONAL_KEYS = [
    "buyer-person",
    "winner-person",
    "winner-touchpoint-name",
    "jury-member-name-lot",
    "participant-name-lot",
    "first-name-ubo",
    "contact-point",
    "touchpoint-email",
    "buyer-phone",
]


def raw_notice(number, title="Construction of a school", country="FRA", cpv="45213150-9", value=500000):
    """One invented raw TED record, dirty with personal data on purpose."""
    return {
        "notice-title": {"eng": title},
        "buyer-name": {"eng": "Ville de Lyon"},
        "buyer-country": country,
        "classification-cpv": [cpv],
        "total-value": value,
        "deadline-receipt-tender-date-lot": "2026-11-30T12:00:00+01:00",
        "publication-number": number,
        "announcement-url": "https://ted.europa.eu/notice/%s" % number,
        # personal data published by the source; must never reach the output
        "buyer-person": "Marie Dupont",
        "winner-person": "Joao Silva",
        "winner-touchpoint-name": "Jean Bernard",
        "jury-member-name-lot": "Ana Costa",
        "participant-name-lot": "Luis Pereira",
        "first-name-ubo": "Helena",
        "contact-point": {
            "name": "Pierre Martin",
            "email": "pierre.martin@lyon.fr",
            "phone": "+33 4 72 00 00 00",
        },
        "touchpoint-email": "marches@lyon.fr",
        "buyer-phone": "+33 4 72 11 11 11",
    }


class FakeTed:
    """Fake transport: records every call and serves invented notices."""

    def __init__(self, notices, total=None, answer_key="notices"):
        self.notices = list(notices)
        self.total = len(self.notices) if total is None else total
        self.answer_key = answer_key
        self.calls = []

    def __call__(self, url, payload):
        self.calls.append({"url": url, "payload": payload})
        page = int(payload.get("page", 1))
        limit = int(payload.get("limit", 100))
        start = (page - 1) * limit
        batch = self.notices[start : start + limit]
        return {self.answer_key: batch, "totalNoticeCount": self.total}


def no_sleep(_seconds):
    return None


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------
@case
def test_body_asks_only_for_the_eight_allowed_fields():
    body = build_search_body("*")
    assert body["fields"] == list(ALLOWED_API_FIELDS)
    assert len(body["fields"]) == 8


@case
def test_body_never_asks_for_a_personal_field():
    body = build_search_body("*", fields=list(ALLOWED_API_FIELDS) + PERSONAL_KEYS)
    for key in PERSONAL_KEYS:
        assert key not in body["fields"], "asked TED for %s" % key
    assert body["fields"] == list(ALLOWED_API_FIELDS)


@case
def test_body_with_only_bad_fields_falls_back_to_the_allow_list():
    body = build_search_body("*", fields=["winner-person", "buyer-person"])
    assert body["fields"] == list(ALLOWED_API_FIELDS)


@case
def test_body_has_query_page_and_limit():
    body = build_search_body("classification-cpv=45000000", page=3, limit=50)
    assert body["query"] == "classification-cpv=45000000"
    assert body["page"] == 3
    assert body["limit"] == 50
    assert sorted(body.keys()) == ["fields", "limit", "page", "query"]


@case
def test_body_clamps_page_and_limit():
    assert build_search_body("*", page=0, limit=0)["page"] == 1
    assert build_search_body("*", page=0, limit=0)["limit"] == 1
    assert build_search_body("*", limit=100000)["limit"] == MAX_PAGE_SIZE


@case
def test_empty_query_becomes_star():
    assert build_search_body("   ")["query"] == "*"


@case
def test_expert_query_joins_cpv_and_country():
    query = build_expert_query(cpv_codes=["45000000-7"], countries=["fra", "DEU"])
    assert "classification-cpv=45000000" in query
    assert "buyer-country=FRA" in query
    assert "buyer-country=DEU" in query
    assert " AND " in query


@case
def test_expert_query_without_filters_is_star():
    assert build_expert_query() == "*"


# ---------------------------------------------------------------------------
# Paging, with the fake transport
# ---------------------------------------------------------------------------
@case
def test_fetch_posts_to_the_official_search_url():
    fake = FakeTed([raw_notice("1-2026")])
    fetch_notices(transport=fake, sleep=no_sleep, max_results=10)
    assert fake.calls[0]["url"] == TED_SEARCH_URL
    assert TED_SEARCH_URL == "https://api.ted.europa.eu/v3/notices/search"


@case
def test_fetch_walks_pages_until_max_results():
    fake = FakeTed([raw_notice("%d-2026" % i) for i in range(1, 7)], total=6)
    got = fetch_notices(transport=fake, sleep=no_sleep, max_results=5, page_size=2)
    assert len(got) == 5
    pages = [c["payload"]["page"] for c in fake.calls]
    assert pages == [1, 2, 3]
    assert fake.calls[-1]["payload"]["limit"] == 1  # only one slot left


@case
def test_fetch_stops_when_total_is_reached():
    fake = FakeTed([raw_notice("%d-2026" % i) for i in range(1, 5)], total=4)
    got = fetch_notices(transport=fake, sleep=no_sleep, max_results=100, page_size=2)
    assert len(got) == 4
    assert [c["payload"]["page"] for c in fake.calls] == [1, 2]


@case
def test_fetch_stops_on_empty_page():
    fake = FakeTed([], total=0)
    got = fetch_notices(transport=fake, sleep=no_sleep, max_results=100)
    assert got == []
    assert len(fake.calls) == 1


@case
def test_fetch_waits_between_pages():
    waits = []
    fake = FakeTed([raw_notice("%d-2026" % i) for i in range(1, 5)], total=10)
    fetch_notices(
        transport=fake,
        sleep=waits.append,
        max_results=4,
        page_size=2,
        delay_seconds=0.25,
    )
    assert waits == [0.25], "expected one pause between the two pages, got %r" % (waits,)


@case
def test_fetch_zero_results_makes_no_call():
    fake = FakeTed([raw_notice("1-2026")])
    assert fetch_notices(transport=fake, sleep=no_sleep, max_results=0) == []
    assert fake.calls == []


@case
def test_fetch_accepts_other_list_keys():
    fake = FakeTed([raw_notice("1-2026")], answer_key="results")
    assert len(fetch_notices(transport=fake, sleep=no_sleep, max_results=10)) == 1


@case
def test_fetch_rejects_a_non_object_answer():
    def broken_transport(url, payload):
        return "<html>error</html>"

    try:
        fetch_notices(transport=broken_transport, sleep=no_sleep, max_results=10)
    except TedApiError:
        return
    raise AssertionError("a non-object answer should raise TedApiError")


@case
def test_fetch_never_sends_a_personal_field_name():
    fake = FakeTed([raw_notice("1-2026")])
    fetch_notices(transport=fake, sleep=no_sleep, max_results=10)
    sent = repr(fake.calls[0]["payload"])
    for key in PERSONAL_KEYS:
        assert key not in sent, "request mentioned %s" % key


# ---------------------------------------------------------------------------
# Retry with a growing wait, still without any network
# ---------------------------------------------------------------------------
class FlakyTed:
    """Fails the first `failures` calls with `error`, then serves one notice."""

    def __init__(self, failures, error):
        self.failures = failures
        self.error = error
        self.calls = 0

    def __call__(self, url, payload):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return {"notices": [raw_notice("1-2026")], "totalNoticeCount": 1}


@case
def test_retry_recovers_from_a_transient_http_error():
    flaky = FlakyTed(2, TedHttpError(503, "service unavailable"))
    got = fetch_notices(transport=flaky, sleep=no_sleep, max_results=5)
    assert flaky.calls == 3, "expected two retries, got %d calls" % flaky.calls
    assert len(got) == 1


@case
def test_retry_recovers_from_a_network_error():
    flaky = FlakyTed(1, TedNetworkError("connection reset"))
    got = fetch_notices(transport=flaky, sleep=no_sleep, max_results=5)
    assert flaky.calls == 2
    assert len(got) == 1


@case
def test_retry_waits_and_the_wait_grows():
    waits = []
    flaky = FlakyTed(2, TedHttpError(429, "too many requests"))
    fetch_notices(
        transport=flaky,
        sleep=waits.append,
        max_results=5,
        backoff_seconds=2.0,
    )
    assert waits == [2.0, 4.0], "expected a doubling wait, got %r" % (waits,)


@case
def test_retry_obeys_a_longer_retry_after_header():
    waits = []
    flaky = FlakyTed(1, TedHttpError(429, "slow down", retry_after=30))
    fetch_notices(transport=flaky, sleep=waits.append, max_results=5, backoff_seconds=2.0)
    assert waits == [30.0]


@case
def test_retry_wait_is_capped():
    huge = TedHttpError(503, "down", retry_after=100000)
    flaky = FlakyTed(1, huge)
    waits = []
    fetch_notices(transport=flaky, sleep=waits.append, max_results=5)
    assert waits == [MAX_BACKOFF_SECONDS]


@case
def test_retry_gives_up_and_reraises_the_real_error():
    always = FlakyTed(99, TedHttpError(503, "still down"))
    try:
        fetch_notices(transport=always, sleep=no_sleep, max_results=5)
    except TedHttpError as exc:
        assert exc.status_code == 503
        assert always.calls == DEFAULT_MAX_ATTEMPTS, "expected %d attempts, got %d" % (
            DEFAULT_MAX_ATTEMPTS,
            always.calls,
        )
        return
    raise AssertionError("a permanent 503 should surface after the last attempt")


@case
def test_a_bad_request_is_not_retried():
    """400 means our query is wrong; retrying it four times only wastes time."""
    always = FlakyTed(99, TedHttpError(400, "bad query"))
    try:
        fetch_notices(transport=always, sleep=no_sleep, max_results=5)
    except TedHttpError:
        assert always.calls == 1, "a 400 was retried %d times" % (always.calls - 1)
        return
    raise AssertionError("a 400 should surface at once")


@case
def test_a_non_object_answer_is_not_retried():
    def broken_transport(url, payload):
        return "<html>error</html>"

    calls = []

    def counting(url, payload):
        calls.append(1)
        return broken_transport(url, payload)

    try:
        fetch_notices(transport=counting, sleep=no_sleep, max_results=5)
    except TedApiError:
        assert len(calls) == 1
        return
    raise AssertionError("a non-object answer should raise TedApiError")


@case
def test_is_retryable_classifies_status_codes():
    for code in (408, 425, 429, 500, 502, 503, 504):
        assert is_retryable(TedHttpError(code)), "%d should be retried" % code
    for code in (400, 401, 403, 404, 405, 422):
        assert not is_retryable(TedHttpError(code)), "%d should not be retried" % code
    assert is_retryable(TedNetworkError("timeout"))
    assert not is_retryable(TedApiError("body is not JSON"))
    assert not is_retryable(ValueError("unrelated"))


@case
def test_retry_after_header_in_date_form_is_ignored():
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert _parse_retry_after(None) is None


@case
def test_retrying_transport_passes_url_and_body_through_untouched():
    seen = []

    def inner(url, payload):
        seen.append((url, payload))
        return {"notices": []}

    wrapped = retrying_transport(inner, sleep=no_sleep)
    wrapped(TED_SEARCH_URL, {"query": "*", "page": 1})
    assert seen == [(TED_SEARCH_URL, {"query": "*", "page": 1})]


class _StubResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.headers = headers or {}

    def json(self):
        return self._payload


def _with_stub_requests(post):
    """Put a fake `requests` module in sys.modules and return the transport.

    `requests_transport` imports `requests` lazily, so this never touches the
    network: the stub records the call and answers whatever the case wants.
    """
    import types

    stub = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    stub.RequestException = RequestException
    stub.post = post
    saved = sys.modules.get("requests")
    sys.modules["requests"] = stub
    try:
        from ted_client import requests_transport

        return requests_transport(timeout_seconds=7.5), stub, saved
    except Exception:
        if saved is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = saved
        raise


def _restore_requests(saved):
    if saved is None:
        sys.modules.pop("requests", None)
    else:
        sys.modules["requests"] = saved


@case
def test_requests_transport_sends_a_timeout_on_every_call():
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return _StubResponse(200, {"notices": []})

    transport, _stub, saved = _with_stub_requests(post)
    try:
        transport(TED_SEARCH_URL, {"query": "*"})
    finally:
        _restore_requests(saved)

    assert seen["timeout"] == 7.5, "no timeout on the request: %r" % (seen.get("timeout"),)
    assert seen["url"] == TED_SEARCH_URL
    assert seen["json"] == {"query": "*"}
    assert seen["headers"]["Accept"] == "application/json"
    assert "User-Agent" in seen["headers"]


@case
def test_requests_transport_turns_a_429_into_a_retryable_error():
    def post(url, **kwargs):
        return _StubResponse(429, headers={"Retry-After": "13"})

    transport, _stub, saved = _with_stub_requests(post)
    try:
        transport(TED_SEARCH_URL, {"query": "*"})
    except TedHttpError as exc:
        assert exc.status_code == 429
        assert exc.retry_after == 13.0
        assert is_retryable(exc)
        return
    finally:
        _restore_requests(saved)
    raise AssertionError("HTTP 429 should raise TedHttpError")


@case
def test_requests_transport_turns_a_timeout_into_a_retryable_error():
    transport = None
    saved = None

    def post(url, **kwargs):
        raise sys.modules["requests"].RequestException("read timed out")

    transport, _stub, saved = _with_stub_requests(post)
    try:
        transport(TED_SEARCH_URL, {"query": "*"})
    except TedNetworkError as exc:
        assert is_retryable(exc)
        return
    finally:
        _restore_requests(saved)
    raise AssertionError("a connection failure should raise TedNetworkError")


@case
def test_client_stays_inside_the_published_ted_limits():
    """700 requests/minute and 3 concurrent downloads, per the reuser page."""
    assert TED_MAX_REQUESTS_PER_MINUTE == 700
    assert TED_MAX_CONCURRENT_DOWNLOADS == 3
    # one request at a time, so concurrency is 1 of the 3 allowed
    assert DEFAULT_DELAY_SECONDS >= 60.0 / TED_MAX_REQUESTS_PER_MINUTE


# ---------------------------------------------------------------------------
# Whole pipeline: fetch -> normalize -> match -> dedupe
# ---------------------------------------------------------------------------
@case
def test_pipeline_output_has_exactly_the_eight_public_keys():
    fake = FakeTed([raw_notice("1-2026")])
    out = collect_matching_notices(transport=fake, sleep=no_sleep, max_results=10)
    assert len(out) == 1
    assert sorted(out[0].keys()) == sorted(OUTPUT_KEYS)
    assert len(out[0]) == 8


@case
def test_pipeline_drops_every_personal_field_of_the_source():
    """Decisive case: the fake answer is full of names, the output has none."""
    fake = FakeTed([raw_notice("1-2026"), raw_notice("2-2026", title="School roof")])
    out = collect_matching_notices(transport=fake, sleep=no_sleep, max_results=10)
    assert len(out) == 2

    blob = repr(out)
    for key in PERSONAL_KEYS:
        assert key not in blob, "leaked key %s" % key
    for value in PERSONAL_VALUES:
        assert value not in blob, "leaked value %s" % value
    assert "+33 4 72 11 11 11" not in blob

    for item in out:
        for key in item:
            lowered = key.lower().replace("_", "-")
            for banned in ("person", "ubo", "contact", "email", "phone", "first-name"):
                assert banned not in lowered, "output key %s contains %s" % (key, banned)

    # and the public fields did survive
    assert out[0]["notice_title"] == "Construction of a school"
    assert out[0]["buyer_name"] == "Ville de Lyon"
    assert out[0]["publication_number"] == "1-2026"
    assert out[0]["deadline"] == "2026-11-30T12:00:00+01:00"
    assert out[0]["announcement_url"] == "https://ted.europa.eu/notice/1-2026"
    assert out[0]["cpv_codes"] == ["45213150-9"]
    assert out[0]["total_value_eur"] == 500000.0


@case
def test_pipeline_applies_the_filters():
    fake = FakeTed(
        [
            raw_notice("1-2026", title="School renovation", country="FRA", cpv="45213150-9", value=300000),
            raw_notice("2-2026", title="Software licences", country="DEU", cpv="72000000-5", value=900000),
            raw_notice("3-2026", title="School canteen", country="FRA", cpv="45213150-9", value=1000),
        ]
    )
    out = collect_matching_notices(
        criteria={
            "cpv_codes": ["45000000"],
            "countries": ["FRA"],
            "min_value_eur": 100000,
            "keywords": ["school"],
        },
        transport=fake,
        sleep=no_sleep,
        max_results=10,
    )
    assert [n["publication_number"] for n in out] == ["1-2026"]


@case
def test_pipeline_dedupes_by_publication_number():
    fake = FakeTed([raw_notice("1-2026"), raw_notice("1-2026"), raw_notice("2-2026")])
    out = collect_matching_notices(transport=fake, sleep=no_sleep, max_results=10)
    assert [n["publication_number"] for n in out] == ["1-2026", "2-2026"]


@case
def test_pipeline_respects_max_results():
    fake = FakeTed([raw_notice("%d-2026" % i) for i in range(1, 11)], total=10)
    out = collect_matching_notices(transport=fake, sleep=no_sleep, max_results=3)
    assert len(out) == 3


@case
def test_pipeline_uses_the_criteria_in_the_query():
    fake = FakeTed([raw_notice("1-2026")])
    collect_matching_notices(
        criteria={"cpv_codes": ["45000000"], "countries": ["FRA"]},
        transport=fake,
        sleep=no_sleep,
        max_results=5,
    )
    query = fake.calls[0]["payload"]["query"]
    assert "classification-cpv=45000000" in query
    assert "buyer-country=FRA" in query


def main():
    passed = 0
    failed = 0
    for fn in CASES:
        try:
            fn()
        except Exception:
            failed += 1
            print("FAIL %s" % fn.__name__)
            traceback.print_exc()
        else:
            passed += 1
            print("ok   %s" % fn.__name__)
    total = passed + failed
    print("no network used: every case injected a fake transport")
    print("%d/%d passed" % (passed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
