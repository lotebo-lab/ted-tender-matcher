"""Checks the .actor JSON files parse and agree with the code that runs.

pytest is not installed in the lab sandbox and `pip install` has no network
there, so this file is a runner of its own, in plain Python. Run it from the
Actor directory:

    ../../.venv/bin/python tests/test_schemas.py

What it verifies:

1. actor.json, dataset_schema.json, output_schema.json and input_schema.json
   are valid JSON;
2. actor.json points at the dataset schema through `storages.dataset` and at
   the output schema through `output`, and both files exist. The missing
   `output` reference is what made the Apify API refuse to make this Actor
   public, with the error `schemas-required`;
3. the output schema is version 1 and every property has a template;
4. the pay-per-event block has exactly one event, `tender-matched` at US$ 0.10,
   it is the name the charging code in src/main.py uses, and no declared event
   is priced at zero (the Apify API answers 400 "Charged event must contain
   price, title, and description" for a free event);
5. every field named in a dataset_schema view, and every field described in the
   output schema, exists in one of the two dataset rows that src/main.py
   actually pushes. The rows are built here by calling the same functions the
   pipeline calls (`matching.normalize_notice` plus `summary_row.tag_notice`
   for a tender, `summary_row.build_summary_row` for the run summary), for a
   full raw notice and for an empty one;
6. the input form fields are the six the code reads in src/main.py DEFAULTS;
7. the summary row: every successful run writes one, it is tagged
   `rowType: summary`, it carries the declared summary fields, it states the
   counts in English when nothing matched, and it charges nothing.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ACTOR_DIR = ROOT / ".actor"
sys.path.insert(0, str(ROOT / "src"))

from matching import normalize_notice  # noqa: E402
from summary_row import (  # noqa: E402
    ROW_TYPE_FIELD,
    SUMMARY_ONLY_FIELDS,
    SUMMARY_ROW_TYPE,
    TENDER_ROW_TYPE,
    build_summary_row,
    tag_notice,
)

FAILURES: list[str] = []
PASSED = 0

SAMPLE_RAW = {
    "notice-title": {"eng": "Construction of a school building"},
    "organisation-name-buyer": {"eng": "Ville de Lyon"},
    "place-of-performance-country": "FRA",
    "classification-cpv": ["45214200", "45000000"],
    "notice-value-eur": "1.250.000,00",
    "deadline-receipt-tender-date-lot": "2026-11-30Z",
    "publication-number": "00123456-2026",
    "links": ["https://ted.europa.eu/en/notice/-/detail/00123456-2026"],
    # personal fields that must never reach the output
    "buyer-person": "Jean Dupont",
    "winner-touchpoint-name": "someone@example.com",
}

EXPECTED_EVENTS = {
    "tender-matched": 0.1,
}

EXPECTED_INPUT = {
    "cpvCodes": ("array", []),
    "countries": ("array", []),
    "minValueEur": ("integer", 0),
    "keywords": ("array", []),
    "publishedSince": ("string", None),
    "maxResults": ("integer", 100),
}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print("ok   " + name)
    else:
        FAILURES.append(name)
        print("FAIL " + name + ((" :: " + detail) if detail else ""))


# --------------------------------------------------------------- 1. valid JSON
loaded: dict[str, dict] = {}
for filename in (
    "actor.json",
    "dataset_schema.json",
    "output_schema.json",
    "input_schema.json",
):
    path = ACTOR_DIR / filename
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        check("valid_json_" + filename, isinstance(parsed, dict), type(parsed).__name__)
        loaded[filename] = parsed if isinstance(parsed, dict) else {}
    except Exception as exc:  # noqa: BLE001 - the message is the report
        loaded[filename] = {}
        check("valid_json_" + filename, False, repr(exc))

actor = loaded["actor.json"]
dataset = loaded["dataset_schema.json"]
output = loaded["output_schema.json"]
inp = loaded["input_schema.json"]

check("actor_name", actor.get("name") == "ted-tender-matcher", repr(actor.get("name")))
check("actor_version", bool(actor.get("version")), repr(actor.get("version")))
check(
    "actor_specification_1",
    actor.get("actorSpecification") == 1,
    repr(actor.get("actorSpecification")),
)

# ------------------------------------------------- 2. the schemas are referenced
storages = actor.get("storages") or {}
check(
    "storages_dataset_reference",
    storages.get("dataset") == "./dataset_schema.json",
    repr(storages.get("dataset")),
)
check(
    "output_schema_reference",
    actor.get("output") == "./output_schema.json",
    repr(actor.get("output")),
)
check(
    "input_schema_reference",
    actor.get("input") == "./input_schema.json",
    repr(actor.get("input")),
)
for reference in (storages.get("dataset"), actor.get("output"), actor.get("input")):
    if isinstance(reference, str):
        target = (ACTOR_DIR / reference).resolve()
        check("referenced_file_exists_" + reference, target.is_file(), str(target))

# ------------------------------------------------------- 3. the output schema
check(
    "output_schema_version_1",
    output.get("actorOutputSchemaVersion") == 1,
    repr(output.get("actorOutputSchemaVersion")),
)
check("output_schema_has_title", bool(output.get("title")), repr(output.get("title")))
check(
    "output_schema_has_properties",
    isinstance(output.get("properties"), dict) and bool(output["properties"]),
    repr(list(output.get("properties", {}))),
)
for prop_name, prop in (output.get("properties") or {}).items():
    check(
        "output_property_has_template_" + prop_name,
        bool(isinstance(prop, dict) and prop.get("template")),
        repr(prop),
    )
    check(
        "output_property_has_title_and_description_" + prop_name,
        bool(isinstance(prop, dict) and prop.get("title") and prop.get("description")),
        repr(prop),
    )

# ------------------------------------------------------ 4. the charged events
events = (actor.get("pay_per_event") or {}).get("actorChargeEvents") or {}
check(
    "charge_events_are_exactly_the_expected_set",
    set(events) == set(EXPECTED_EVENTS),
    repr(sorted(events)),
)
for event_name, price in EXPECTED_EVENTS.items():
    spec = events.get(event_name) or {}
    check(
        "charge_event_price_%s_is_%s" % (event_name, price),
        spec.get("eventPriceUsd") == price,
        repr(spec.get("eventPriceUsd")),
    )
    check(
        "charge_event_has_title_" + event_name,
        bool(spec.get("eventTitle")) and bool(spec.get("eventDescription")),
        repr(spec),
    )
# The Apify API refuses a charged event without a price: it answers 400 with
# "Charged event must contain price, title, and description". A free
# `actor-start` therefore cannot be declared here, and the code must not try to
# charge it either.
for event_name, spec in events.items():
    check(
        "charge_event_price_above_zero_" + event_name,
        isinstance(spec.get("eventPriceUsd"), (int, float))
        and spec.get("eventPriceUsd") > 0,
        repr(spec.get("eventPriceUsd")),
    )

main_source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
check(
    "main_py_uses_tender_matched_event",
    'CHARGE_EVENT = "tender-matched"' in main_source,
    "constant not found in src/main.py",
)
check(
    "main_py_pushes_normalized_notices",
    "await Actor.push_data(tag_notice(notice))" in main_source,
    "src/main.py no longer pushes normalized notices; this test's model is stale",
)
check(
    "main_py_writes_summary_record",
    'await Actor.set_value("SUMMARY", summary)' in main_source,
    "src/main.py no longer writes the SUMMARY record",
)
# The defect this file guards against: a run that matched nothing used to end
# SUCCEEDED with an empty dataset. The summary row goes to the DATASET, not
# only to the key-value store.
check(
    "main_py_pushes_the_summary_row_to_the_dataset",
    "await Actor.push_data(build_summary_row(report))" in main_source,
    "src/main.py must push the summary row into the dataset on every run",
)
check(
    "summary_row_never_charges",
    "charge"
    not in main_source.split("await Actor.push_data(build_summary_row(report))")[1].split(
        "set_value"
    )[0],
    "no charge call may sit between the summary row and the end of the run",
)
check(
    "summary_row_is_pushed_after_the_charging_loop",
    main_source.index("await Actor.push_data(build_summary_row(report))")
    > main_source.index("await Actor.push_data(tag_notice(notice))"),
    "the summary row must be written after the tender rows",
)

# ------------------------- 5. every declared field exists in the row main.py pushes
row_full = tag_notice(normalize_notice(SAMPLE_RAW))
row_empty = tag_notice(normalize_notice({}))
row_fields = set(row_full) & set(row_empty)
check("dataset_row_has_fields", bool(row_fields), repr(sorted(row_fields)))
check(
    "dataset_row_is_tagged_tender",
    row_full.get(ROW_TYPE_FIELD) == TENDER_ROW_TYPE,
    repr(row_full.get(ROW_TYPE_FIELD)),
)
check(
    "dataset_row_has_no_personal_field",
    "buyer-person" not in row_full and "winner-touchpoint-name" not in row_full,
    repr(sorted(row_full)),
)

# ---------------------------------------------- 7. the summary row of a run
# The run this models is the one that broke the promise: the search worked,
# TED answered 120 notices and the buyer's CPV filter matched none of them.
EMPTY_RUN = {
    "noticesRead": 120,
    "matchedNotices": 0,
    "storedNotices": 0,
    "chargedEvents": 0,
    "chargeLimitReached": False,
    "chargeFailures": 0,
    "criteriaUsed": {
        "cpv_codes": ["45214200"],
        "countries": ["PRT"],
        "min_value_eur": 0,
        "keywords": [],
    },
}
summary_row = build_summary_row(EMPTY_RUN)
check(
    "summary_row_is_tagged_summary",
    summary_row[ROW_TYPE_FIELD] == SUMMARY_ROW_TYPE,
    repr(summary_row.get(ROW_TYPE_FIELD)),
)
check(
    "summary_row_has_exactly_the_declared_summary_fields",
    set(summary_row) == {ROW_TYPE_FIELD} | set(SUMMARY_ONLY_FIELDS),
    "row only: %s / declared only: %s"
    % (
        sorted(set(summary_row) - ({ROW_TYPE_FIELD} | set(SUMMARY_ONLY_FIELDS))),
        sorted(({ROW_TYPE_FIELD} | set(SUMMARY_ONLY_FIELDS)) - set(summary_row)),
    ),
)
check(
    "summary_row_of_a_run_with_no_match_states_the_counts",
    summary_row["noticesRead"] == 120
    and summary_row["matchedNotices"] == 0
    and summary_row["storedNotices"] == 0,
    repr(summary_row),
)
check(
    "summary_message_of_a_run_with_no_match_says_so_in_english",
    "No tender matched" in summary_row["message"]
    and "120 notice(s) were read from TED" in summary_row["message"],
    repr(summary_row["message"]),
)
check(
    "summary_message_names_the_filters_that_returned_nothing",
    "45214200" in summary_row["message"] and "PRT" in summary_row["message"],
    repr(summary_row["message"]),
)
check(
    "summary_row_of_a_run_with_no_match_charged_nothing",
    summary_row["chargedEvents"] == 0,
    repr(summary_row["chargedEvents"]),
)
matched_row = build_summary_row(
    {**EMPTY_RUN, "matchedNotices": 3, "storedNotices": 3, "chargedEvents": 3}
)
check(
    "summary_message_of_a_run_with_matches_counts_them",
    "3 of the 120 notice(s) read from TED matched" in matched_row["message"],
    repr(matched_row["message"]),
)
check(
    "summary_row_reports_a_spent_charge_limit",
    "charge limit"
    in build_summary_row(
        {**EMPTY_RUN, "matchedNotices": 3, "storedNotices": 1, "chargeLimitReached": True}
    )["message"],
    repr(
        build_summary_row(
            {**EMPTY_RUN, "matchedNotices": 3, "storedNotices": 1, "chargeLimitReached": True}
        )["message"]
    ),
)
check(
    "summary_row_carries_a_date",
    isinstance(summary_row.get("finishedAt"), str)
    and summary_row["finishedAt"].startswith("20"),
    repr(summary_row.get("finishedAt")),
)

# Everything the dataset can hold: the tagged tender row plus the summary row.
all_row_fields = row_fields | set(summary_row)

views = dataset.get("views") or {}
check("dataset_schema_has_views", bool(views), repr(sorted(views)))
for view_name, view in views.items():
    check("view_has_title_" + view_name, bool(view.get("title")), repr(view))
    fields = ((view.get("transformation") or {}).get("fields")) or []
    check("view_lists_fields_" + view_name, bool(fields), repr(view))
    missing = [field for field in fields if field not in all_row_fields]
    check(
        "view_fields_exist_in_output_" + view_name,
        not missing,
        "not written by src/main.py: " + repr(missing),
    )
    check(
        "view_shows_the_row_type_" + view_name,
        ROW_TYPE_FIELD in fields,
        "a reader must be able to tell a tender from the summary: " + repr(fields),
    )
    check(
        "view_shows_the_summary_message_" + view_name,
        "message" in fields,
        "the summary row must be readable in the table view: " + repr(fields),
    )
    display_props = set(((view.get("display") or {}).get("properties") or {}))
    check(
        "view_display_matches_fields_" + view_name,
        display_props <= set(fields),
        "displayed but not selected: " + repr(sorted(display_props - set(fields))),
    )

# The output schema describes the dataset in prose: every field name it names
# must be a field the code really writes, and no field of the row may be left out.
tenders_description = ((output.get("properties") or {}).get("tenders") or {}).get(
    "description", ""
)
named = [field for field in row_fields if field in tenders_description]
check(
    "output_description_names_every_row_field",
    set(named) == row_fields,
    "missing from the description: " + repr(sorted(row_fields - set(named))),
)

# The dataset schema declares the shape of the rows: both row types must be in
# it, or the summary row sits outside the contract the buyer reads.
declared_fields = set(((dataset.get("fields") or {}).get("properties") or {}))
check(
    "dataset_schema_declares_every_field_of_both_rows",
    all_row_fields <= declared_fields,
    "not declared in dataset_schema.json: " + repr(sorted(all_row_fields - declared_fields)),
)
check(
    "dataset_schema_row_type_enum_lists_both_rows",
    set(
        ((dataset.get("fields") or {}).get("properties") or {})
        .get(ROW_TYPE_FIELD, {})
        .get("enum", [])
    )
    == {TENDER_ROW_TYPE, SUMMARY_ROW_TYPE},
    repr(
        ((dataset.get("fields") or {}).get("properties") or {})
        .get(ROW_TYPE_FIELD, {})
        .get("enum")
    ),
)

summary_description = ((output.get("properties") or {}).get("summary") or {}).get(
    "description", ""
)
check(
    "output_description_mentions_the_summary_row_in_the_dataset",
    "summary row" in tenders_description,
    repr(tenders_description),
)
for key in (
    "noticesRead",
    "matchedNotices",
    "storedNotices",
    "chargedEvents",
    "chargeLimitReached",
    "chargeFailures",
):
    check(
        "summary_description_names_" + key,
        key in summary_description and ('"%s"' % key) in main_source,
        "the SUMMARY record and its description disagree about " + key,
    )

# ----------------------------------------- 6. input form matches the code
props = inp.get("properties") or {}
check("input_schema_version_1", inp.get("schemaVersion") == 1, repr(inp.get("schemaVersion")))
check("input_fields_are_the_six", set(props) == set(EXPECTED_INPUT), repr(sorted(props)))
for field, (ftype, default) in EXPECTED_INPUT.items():
    spec = props.get(field) or {}
    check("input_type_" + field, spec.get("type") == ftype, repr(spec.get("type")))
    check("input_has_description_" + field, bool(spec.get("description")), repr(spec))
    if default is not None:
        check(
            "input_default_%s_is_%s" % (field, default),
            spec.get("default") == default,
            repr(spec.get("default")),
        )

TOTAL = PASSED + len(FAILURES)
if FAILURES:
    print("\nfailed: " + ", ".join(FAILURES))
print("%d/%d passed" % (PASSED, TOTAL))
sys.exit(1 if FAILURES else 0)
