"""The dataset never comes back empty from a run that succeeded.

This is the regression test for the defect measured on 20/09/2026: the Actor
pushed only the notices that matched (`src/main.py`, `await Actor.push_data`),
and the run totals went to `Actor.set_value("SUMMARY", ...)`, which is the
key-value store and NOT the file the buyer opens. A buyer with a narrow CPV
code therefore got a run that ended SUCCEEDED with an empty dataset and not one
word explaining why.

The test drives `src/main.py` itself, with a fake `apify` module in
`sys.modules`, so what it proves is that `Actor.push_data` really receives the
summary row, not just that a helper can build one. No network is used:
`ted_client.fetch_notices` is answered in memory with raw TED-shaped records,
while the real normalizer, the real filters and the real dedupe run.

Two runs are checked:

1. filters that match nothing: zero tender rows, one summary row, zero charges;
2. filters that match two notices: two tender rows plus the summary row, two
   charges, and the summary row charges nothing on top of them.

Run:

    .venv/bin/python negocios/actor-licitacoes-ted/tests/test_summary_row.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def raw_notice(number: str, title: str, country: str, cpv: str, value: str) -> dict:
    """A raw TED record, with personal fields the allow list must drop."""
    return {
        "notice-title": {"eng": title},
        "organisation-name-buyer": {"eng": "Ville de Lyon"},
        "place-of-performance-country": country,
        "classification-cpv": [cpv],
        "notice-value-eur": value,
        "deadline-receipt-tender-date-lot": "2026-11-30Z",
        "publication-number": number,
        "links": ["https://ted.europa.eu/en/notice/-/detail/%s" % number],
        "buyer-person": "Jean Dupont",
        "winner-touchpoint-name": "someone@example.com",
    }


# Three construction notices in France. Nothing here is Portuguese and nothing
# is about software, which is what makes run 1 match zero of them.
TED_PAGE = [
    raw_notice("00000001-2026", "Construction of a school building", "FRA",
               "45214200", "1.250.000,00"),
    raw_notice("00000002-2026", "Construction of a sports hall", "FRA",
               "45212200", "800.000,00"),
    raw_notice("00000003-2026", "School roof repair", "FRA",
               "45261900", "120.000,00"),
]


class FakeLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def _add(self, prefix: str, message, args) -> None:  # noqa: ANN001
        text = str(message)
        if args:
            try:
                text = text % args
            except Exception:  # noqa: BLE001 - the log line is not the test
                pass
        self.lines.append(prefix + text)

    def info(self, message, *args) -> None:  # noqa: ANN001
        self._add("", message, args)

    def warning(self, message, *args) -> None:  # noqa: ANN001
        self._add("[warn] ", message, args)

    def error(self, message, *args) -> None:  # noqa: ANN001
        self._add("[error] ", message, args)

    def exception(self, message, *args) -> None:  # noqa: ANN001
        self._add("[exc] ", message, args)


class FakeChargeResult:
    def __init__(self) -> None:
        self.event_charge_limit_reached = False
        self.charged_count = 1


class FakePricing:
    is_pay_per_event = True
    pricing_model = "PAY_PER_EVENT"


class FakeChargingManager:
    def get_pricing_info(self):  # noqa: ANN201
        return FakePricing()


class FakeConfiguration:
    default_dataset_id = "FAKE_DATASET"


class FakeActor:
    """The slice of the Apify SDK that `src/main.py` touches."""

    def __init__(self, actor_input: dict) -> None:
        self.input = actor_input
        self.log = FakeLog()
        self.configuration = FakeConfiguration()
        self.pushed: list[dict] = []
        self.push_calls = 0
        self.values: dict = {}
        self.charges = 0
        # (rows pushed so far, charges so far) at the moment of each charge,
        # so the test can prove no charge happened after the summary row.
        self.charge_marks: list[int] = []

    async def __aenter__(self):  # noqa: ANN201
        return self

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN001
        return False

    async def get_input(self) -> dict:
        return self.input

    def get_charging_manager(self) -> FakeChargingManager:
        return FakeChargingManager()

    async def charge(self, event_name: str, count: int = 1):  # noqa: ANN201
        self.charges += count
        self.charge_marks.append(len(self.pushed))
        return FakeChargeResult()

    async def push_data(self, rows) -> None:  # noqa: ANN001
        self.push_calls += 1
        self.pushed.extend(rows if isinstance(rows, list) else [rows])

    async def set_value(self, key: str, value) -> None:  # noqa: ANN001
        self.values[key] = value


def run_actor(actor_input: dict, page: list[dict]) -> FakeActor:
    """Import and run `src/main.py` with TED answered from memory."""
    actor = FakeActor(actor_input)
    fake_module = types.ModuleType("apify")
    fake_module.Actor = actor
    sys.modules["apify"] = fake_module
    for name in ("main", "ted_client", "matching", "summary_row"):
        sys.modules.pop(name, None)

    import ted_client  # noqa: PLC0415

    calls: list[dict] = []

    def fake_fetch_notices(**kwargs):  # noqa: ANN001, ANN202
        calls.append(kwargs)
        return list(page)

    real_fetch = ted_client.fetch_notices
    ted_client.fetch_notices = fake_fetch_notices
    try:
        import main  # noqa: PLC0415

        asyncio.run(main.main())
    finally:
        ted_client.fetch_notices = real_fetch
        sys.modules.pop("apify", None)
    actor.ted_calls = calls  # type: ignore[attr-defined]
    return actor


def scenario_nothing_matches() -> list[tuple[str, bool, str]]:
    print("--- scenario 1: the filters match none of the notices read ---")
    actor = run_actor(
        {
            "cpvCodes": ["45214200"],
            "countries": ["PRT"],
            "minValueEur": 0,
            "keywords": [],
            "maxResults": 50,
        },
        TED_PAGE,
    )
    for line in actor.log.lines:
        print("  " + str(line))

    rows = actor.pushed
    summary_rows = [r for r in rows if r.get("rowType") == "summary"]
    tender_rows = [r for r in rows if r.get("rowType") == "tender"]
    summary = summary_rows[0] if summary_rows else {}

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    check("the run really matched nothing", not tender_rows,
          str([r.get("publication_number") for r in tender_rows]))
    check("THE DATASET IS NOT EMPTY", len(rows) >= 1, "rows=%d" % len(rows))
    check("push_data was called even with zero matches", actor.push_calls == 1,
          "push_calls=%d" % actor.push_calls)
    check("exactly one summary row", len(summary_rows) == 1,
          "summary rows=%d" % len(summary_rows))
    check("the summary row is tagged", summary.get("rowType") == "summary",
          repr(summary.get("rowType")))
    check("the summary row says how many notices were read from TED",
          summary.get("noticesRead") == len(TED_PAGE), repr(summary.get("noticesRead")))
    check("the summary row says zero matched", summary.get("matchedNotices") == 0,
          repr(summary.get("matchedNotices")))
    check("the summary row says zero stored", summary.get("storedNotices") == 0,
          repr(summary.get("storedNotices")))
    check("the summary row explains the zero in English",
          "No tender matched" in summary.get("message", "")
          and "3 notice(s) were read from TED" in summary.get("message", ""),
          repr(summary.get("message")))
    check("the summary row names the filters that returned nothing",
          "45214200" in summary.get("criteria", "")
          and "PRT" in summary.get("criteria", ""),
          repr(summary.get("criteria")))
    check("the summary row carries a date",
          isinstance(summary.get("finishedAt"), str)
          and str(summary.get("finishedAt")).startswith("20"),
          repr(summary.get("finishedAt")))
    check("NOTHING WAS CHARGED for a run with no match", actor.charges == 0,
          "charges=%d" % actor.charges)
    check("the summary row reports what was charged",
          summary.get("chargedEvents") == actor.charges,
          "row=%s actor=%d" % (summary.get("chargedEvents"), actor.charges))
    check("the SUMMARY key-value record still exists", "SUMMARY" in actor.values)
    check("the key-value record and the row agree on the counts",
          actor.values.get("SUMMARY", {}).get("noticesRead") == summary.get("noticesRead")
          and actor.values.get("SUMMARY", {}).get("matchedNotices")
          == summary.get("matchedNotices"))
    check("the dataset row count matches what the log claims it wrote",
          any("Wrote %d row(s)" % len(rows) in str(line) for line in actor.log.lines),
          str([line for line in actor.log.lines if "Wrote " in str(line)]))
    check("no personal field reached the dataset",
          not any(key in row for row in rows
                  for key in ("buyer-person", "winner-touchpoint-name")))

    print("\n  dataset rows: %d (%d tender, %d summary); charged events: %d"
          % (len(rows), len(tender_rows), len(summary_rows), actor.charges))
    print("  summary row: %s\n" % summary)
    return checks


def scenario_two_match() -> list[tuple[str, bool, str]]:
    print("--- scenario 2: two of the three notices match ---")
    actor = run_actor(
        {
            "cpvCodes": ["45000000"],
            "countries": ["FRA"],
            "minValueEur": 200000,
            "keywords": ["construction"],
            "maxResults": 50,
        },
        TED_PAGE,
    )
    for line in actor.log.lines:
        print("  " + str(line))

    rows = actor.pushed
    summary_rows = [r for r in rows if r.get("rowType") == "summary"]
    tender_rows = [r for r in rows if r.get("rowType") == "tender"]
    summary = summary_rows[0] if summary_rows else {}

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    check("the two construction tenders are reported", len(tender_rows) == 2,
          str([r.get("notice_title") for r in tender_rows]))
    check("the notice below the minimum value was filtered out",
          all(r.get("publication_number") != "00000003-2026" for r in tender_rows),
          str([r.get("publication_number") for r in tender_rows]))
    check("every row says what type it is",
          all(r.get("rowType") in {"tender", "summary"} for r in rows))
    check("the summary row is the last one", rows[-1].get("rowType") == "summary",
          repr(rows[-1].get("rowType")))
    check("a reader dropping the summary keeps every tender",
          len([r for r in rows if r.get("rowType") != "summary"]) == len(tender_rows))
    check("the summary counts the tenders",
          summary.get("matchedNotices") == len(tender_rows)
          and summary.get("storedNotices") == len(tender_rows),
          repr(summary))
    check("the summary message counts what was read and what matched",
          "2 of the 3 notice(s) read from TED matched" in summary.get("message", ""),
          repr(summary.get("message")))
    check("one charge per stored tender, and no more",
          actor.charges == len(tender_rows), "charges=%d" % actor.charges)
    check("THE SUMMARY ROW ADDS NO CHARGE",
          all(mark < len(rows) - 1 for mark in actor.charge_marks),
          "rows pushed before each charge: %s of %d" % (actor.charge_marks, len(rows)))
    check("a tender row has no summary-only field",
          all("message" not in r and "noticesRead" not in r for r in tender_rows))

    print("\n  dataset rows: %d (%d tender, %d summary); charged events: %d"
          % (len(rows), len(tender_rows), len(summary_rows), actor.charges))
    print("  summary row: %s\n" % summary)
    return checks


def main() -> int:
    checks = scenario_nothing_matches() + scenario_two_match()
    failed = 0
    for name, ok, detail in checks:
        print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                              ("  (%s)" % detail) if detail and not ok else ""))
        failed += 0 if ok else 1
    print("\n%d/%d checks passed" % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
