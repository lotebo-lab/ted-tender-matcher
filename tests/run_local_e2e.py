"""End-to-end run of the TED Tender Matcher against a recorded answer.

Why this script exists
----------------------
The lab sandbox does not reach `api.ted.europa.eu`, so the Actor cannot call
the live search endpoint here. Everything else in the run is the real thing:
the real `src/main.py` entry point, the real Apify Actor lifecycle (input,
dataset, key-value store), the real request building, paging and retry logic in
`src/ted_client.py` and the real allow list and filters in `src/matching.py`.

The only substitution is the transport underneath `requests`: an adapter
answers the POST from `tests/fixtures/ted_search_page1.json`, a response body
written by hand against the official TED field list
(https://docs.ted.europa.eu/ODS/latest/reuse/field-list.html), including
personal fields that the allow list must drop. Page 2 answers an empty list, so
the paging loop ends the way it would against TED.

What this run proves: the shape of the dataset rows the Actor produces and the
filters it applies. What it does not prove: that the live TED API answers in
this shape. Only a cloud run can prove that.

Usage:
    python tests/run_local_e2e.py <run-dir>
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
from pathlib import Path

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

HERE = Path(__file__).resolve().parent
ACTOR_DIR = HERE.parent
sys.path.insert(0, str(ACTOR_DIR / "src"))

TED_PREFIX = "https://api.ted.europa.eu"
FIXTURE = HERE / "fixtures" / "ted_search_page1.json"


class RecordedTedAdapter(BaseAdapter):
    """Answers the TED search POST from a file, page by page."""

    def __init__(self, fixture: Path) -> None:
        super().__init__()
        self.fixture = fixture
        self.calls: list[dict] = []

    def send(self, request, stream=False, timeout=None, verify=True, cert=None,
             proxies=None):  # noqa: D102 - requests adapter interface
        body = json.loads(request.body.decode("utf-8") if isinstance(request.body, bytes)
                          else request.body)
        self.calls.append(body)
        print(f"[ted] POST {request.url} body={json.dumps(body, sort_keys=True)}",
              flush=True)

        if int(body.get("page", 1)) == 1:
            payload = json.loads(self.fixture.read_text())
        else:
            payload = {"notices": [], "totalNoticeCount": 0}

        raw = json.dumps(payload).encode("utf-8")
        response = requests.Response()
        response.status_code = 200
        response.reason = "OK"
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        response.headers = CaseInsensitiveDict(
            {
                "Server": "recorded-fixture/1.0 (no network: sandbox denies TED)",
                "Content-Type": "application/json",
                "Content-Length": str(len(raw)),
            }
        )
        response.raw = io.BytesIO(raw)
        print(f"[ted] 200 {len(raw)} bytes, "
              f"{len(payload.get('notices', []))} notice(s) in the page", flush=True)
        return response

    def close(self) -> None:
        return None


def install_adapter(fixture: Path) -> RecordedTedAdapter:
    adapter = RecordedTedAdapter(fixture)
    original_get_adapter = requests.Session.get_adapter

    def get_adapter(self, url):  # noqa: ANN001 - patching requests
        if url.startswith(TED_PREFIX):
            return adapter
        return original_get_adapter(self, url)

    requests.Session.get_adapter = get_adapter
    return adapter


def main() -> int:
    run_dir = Path(sys.argv[1]).resolve()

    actor_input = {
        "cpvCodes": ["45000000"],
        "countries": ["DEU", "PRT"],
        "minValueEur": 200000,
        "keywords": ["school"],
        "maxResults": 50,
    }

    input_dir = run_dir / "storage" / "key_value_stores" / "default"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "INPUT.json").write_text(json.dumps(actor_input, indent=2))
    os.chdir(run_dir)

    print(f"Actor dir: {ACTOR_DIR}")
    print(f"Run dir:   {run_dir}")
    print(f"Fixture:   {FIXTURE}")
    print("The POST to https://api.ted.europa.eu/v3/notices/search is answered "
          "from that file (the sandbox has no route to TED).")
    print(f"Actor input: {json.dumps(actor_input)}")
    print("-" * 72, flush=True)

    adapter = install_adapter(FIXTURE)

    import main as actor_main  # noqa: PLC0415 - after sys.path setup

    actor_exit_code = 0
    try:
        asyncio.run(actor_main.main())
    except SystemExit as exc:
        actor_exit_code = 0 if exc.code is None else int(exc.code)

    print("-" * 72, flush=True)
    print(f"Actor lifecycle exit code: {actor_exit_code}")
    print(f"POST calls to the TED endpoint: {len(adapter.calls)}")

    dataset_dir = run_dir / "storage" / "datasets" / "default"
    rows = []
    for item in sorted(dataset_dir.glob("[0-9]*.json")):
        rows.append(json.loads(item.read_text()))
    print(f"Dataset rows written: {len(rows)}")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))

    kvs_dir = run_dir / "storage" / "key_value_stores" / "default"
    summary_file = kvs_dir / "SUMMARY"
    if not summary_file.is_file():
        summary_file = kvs_dir / "SUMMARY.json"
    if summary_file.is_file():
        print("SUMMARY record:")
        print(json.dumps(json.loads(summary_file.read_text()), indent=2,
                         ensure_ascii=False))
    else:
        print(f"SUMMARY record not found at {summary_file}")

    # The allow list is the LGPD guard of this Actor: prove in the log that no
    # personal field of the source reached the dataset.
    source = json.loads(FIXTURE.read_text())
    personal = [
        "buyer-person", "winner-person", "first-name-ubo", "contact-email",
        "contact-telephone", "jury-member-name-lot", "winner-touchpoint-name",
    ]
    present = sorted({key for notice in source["notices"] for key in notice
                      if key in personal})
    leaked = sorted({key for row in rows for key in row if key in personal})
    flat = json.dumps(rows, ensure_ascii=False)
    values_leaked = [value for notice in source["notices"] for key, value in notice.items()
                     if key in personal and isinstance(value, str) and value in flat]
    print(f"Personal fields in the source response: {present}")
    print(f"Personal field names in the dataset: {leaked}")
    print(f"Personal field values found in the dataset: {values_leaked}")
    print(f"exit_code {actor_exit_code}")
    return actor_exit_code


if __name__ == "__main__":
    sys.exit(main())
