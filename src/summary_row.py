"""The one dataset row every successful run writes, matches or none.

Why this file exists
--------------------
This Actor filters TED notices. A buyer with a narrow CPV code, a small country
list or a high minimum value gets zero matches often, and that is a normal
answer, not a failure. Until now such a run finished SUCCEEDED and handed back
an EMPTY dataset: from the buyer's side that is indistinguishable from an Actor
that crashed, and nothing in the file he opens says how many notices were read
or why none of them was kept. The summary row says it inside the dataset
itself.

Nothing here charges anything. The module has no Apify import on purpose: it is
pure data built after the work is done. The only charged event of this Actor,
`tender-matched`, is charged in `src/main.py` once per matching notice, before
that notice is pushed, and the summary row is pushed after that loop without
any charge. A run with zero matches therefore stores this row and is charged
nothing beyond the platform's own `actor-start`.

Same design as `negocios/actor-links-quebrados` (SUMMARY_ROW_TYPE,
`build_summary_row`) and `negocios/actor-vigia-feeds`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

# Every row carries this field so a reader tells a tender apart from the run
# summary with one comparison, and drops the summary when it only wants tenders.
ROW_TYPE_FIELD = "rowType"
TENDER_ROW_TYPE = "tender"
SUMMARY_ROW_TYPE = "summary"

# Fields that exist only on the summary row. Declared here so the schema test
# checks the dataset schema against the code instead of against a list typed by
# hand twice.
SUMMARY_ONLY_FIELDS = (
    "finishedAt",
    "noticesRead",
    "matchedNotices",
    "storedNotices",
    "chargedEvents",
    "chargeLimitReached",
    "chargeFailures",
    "criteria",
    "message",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tag_notice(notice: dict) -> dict:
    """Stamp `rowType` on one tender row, without touching its content.

    A copy is returned: `matching.normalize_notice` writes exactly the eight
    allowed fields and that allow list is the LGPD guard of this Actor, so the
    row type is added here, at the door of the dataset, never inside the
    normalizer.
    """
    return {ROW_TYPE_FIELD: TENDER_ROW_TYPE, **(notice or {})}


def describe_criteria(criteria: Dict[str, Any] | None) -> str:
    """The filters of this run in one short, readable clause."""
    criteria = criteria or {}
    parts: List[str] = []
    cpv = [str(c) for c in (criteria.get("cpv_codes") or [])]
    countries = [str(c) for c in (criteria.get("countries") or [])]
    keywords = [str(k) for k in (criteria.get("keywords") or [])]
    min_value = criteria.get("min_value_eur") or 0

    parts.append("CPV " + (", ".join(cpv) if cpv else "any"))
    parts.append("countries " + (", ".join(countries) if countries else "any"))
    parts.append(
        "minimum value EUR %s" % (int(min_value) if min_value else 0)
    )
    parts.append("keywords " + (", ".join(keywords) if keywords else "none"))
    return "; ".join(parts)


def summary_message(report: Dict[str, Any]) -> str:
    """One plain sentence with the counts. No promise, no advice beyond facts."""
    read = report.get("noticesRead", 0) or 0
    matched = report.get("matchedNotices", 0) or 0
    stored = report.get("storedNotices", 0) or 0
    criteria = report.get("criteria") or describe_criteria(report.get("criteriaUsed"))

    if read == 0:
        text = (
            "No notice was read from TED for this search, so this run says "
            "nothing about whether a matching tender exists. Filters used: "
            "%s." % criteria
        )
    elif matched == 0:
        text = (
            "No tender matched: %d notice(s) were read from TED and none of "
            "them matched your filters (%s). The search itself worked, so this "
            "dataset has no tender row on purpose; widen the filters (fewer "
            "CPV codes, more countries, a lower minimum value or fewer "
            "keywords) and run again." % (read, criteria)
        )
    else:
        text = (
            "%d of the %d notice(s) read from TED matched your filters (%s), "
            "and %d of them were stored in this dataset."
            % (matched, read, criteria, stored)
        )

    if matched == 0:
        text += (
            " This Actor charges per matching tender, so no tender event was "
            "charged for this run."
        )
    elif stored < matched:
        text += (
            " %d matching tender(s) were not stored because the run stopped "
            "early." % (matched - stored)
        )
    if report.get("chargeLimitReached"):
        text += (
            " The run stopped early because it reached its pay-per-event "
            "charge limit; raise the limit to receive the rest."
        )
    return text


def build_summary_row(report: Dict[str, Any]) -> dict:
    """The summary row, built from the same numbers the SUMMARY record holds."""
    criteria = report.get("criteria") or describe_criteria(report.get("criteriaUsed"))
    return {
        ROW_TYPE_FIELD: SUMMARY_ROW_TYPE,
        "finishedAt": report.get("finishedAt") or _now(),
        "noticesRead": report.get("noticesRead", 0) or 0,
        "matchedNotices": report.get("matchedNotices", 0) or 0,
        "storedNotices": report.get("storedNotices", 0) or 0,
        "chargedEvents": report.get("chargedEvents", 0) or 0,
        "chargeLimitReached": bool(report.get("chargeLimitReached", False)),
        "chargeFailures": report.get("chargeFailures", 0) or 0,
        "criteria": criteria,
        "message": summary_message({**report, "criteria": criteria}),
    }


__all__ = (
    "ROW_TYPE_FIELD",
    "TENDER_ROW_TYPE",
    "SUMMARY_ROW_TYPE",
    "SUMMARY_ONLY_FIELDS",
    "tag_notice",
    "describe_criteria",
    "summary_message",
    "build_summary_row",
)
