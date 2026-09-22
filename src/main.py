"""Apify Actor entry point: EU public tender alerts (TED).

Input is a set of filters (CPV codes, buyer countries, minimum value, title
keywords). The Actor queries the public TED Search API, keeps the notices that
match, and pushes one dataset item per matching notice with the eight public
fields of that notice.

The fetching and matching logic lives in `ted_client.py` and `matching.py`,
which have no Apify dependency, so they run and are tested locally without
network.

Privacy: `matching.normalize_notice` is an allow list of eight non-personal
fields. No name, e-mail or phone of a person ever reaches the dataset.
"""

from __future__ import annotations

import asyncio

from apify import Actor

try:  # running inside the Actor image (python src/main.py)
    from summary_row import (
        SUMMARY_ROW_TYPE,
        build_summary_row,
        describe_criteria,
        tag_notice,
    )
    from ted_client import collect_matching_notices
except ImportError:  # running as a package (python -m src.main)
    from .summary_row import (
        SUMMARY_ROW_TYPE,
        build_summary_row,
        describe_criteria,
        tag_notice,
    )
    from .ted_client import collect_matching_notices


# Pay-per-event: one charged event per matching tender stored in the dataset.
# The Actor never charges `actor-start` in code; Apify charges that one itself.
CHARGE_EVENT = "tender-matched"

# Hard ceiling for one charge round trip. On the platform every charge is an
# HTTP call to the Apify API. A charge without a timeout is exactly what killed
# our first published Actor in the cloud: one hung call blocked the run until
# the platform timeout and nothing was stored. A charge that does not answer in
# time is treated like any other charging failure: warn and keep going.
CHARGE_TIMEOUT_SECONDS = 5.0

DEFAULTS = {
    "cpvCodes": [],
    "countries": [],
    "minValueEur": 0,
    "keywords": [],
    "maxResults": 100,
    "publishedSince": None,
}


async def charge_one(state: dict) -> bool:
    """Charge one `tender-matched` event. False means: stop pushing more.

    The only reason to answer False is a real pay-per-event limit: the user's
    budget for this run is spent. A timeout, an API hiccup or charging being
    off lets the run continue, because the work is already done or free.
    """
    if state["limit_reached"]:
        return False
    try:
        result = await asyncio.wait_for(
            Actor.charge(event_name=CHARGE_EVENT),
            timeout=CHARGE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        state["failures"] += 1
        Actor.log.warning(
            "Charging %s timed out after %.0f s; continuing the run."
            % (CHARGE_EVENT, CHARGE_TIMEOUT_SECONDS)
        )
        return True
    except Exception as exc:  # noqa: BLE001 - never kill a paid run
        state["failures"] += 1
        Actor.log.warning("Could not charge %s: %s" % (CHARGE_EVENT, exc))
        return True

    if getattr(result, "event_charge_limit_reached", False):
        state["limit_reached"] = True
        Actor.log.info(
            "Charge limit reached. Keeping every tender stored so far and "
            "stopping here."
        )
        return False
    charged = getattr(result, "charged_count", 0) or 0
    if charged < 1:
        # Not a limit: the platform ignored the charge. Keep going.
        state["failures"] += 1
        return True
    state["charged"] += charged
    return True


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        options = {**DEFAULTS, **{k: v for k, v in actor_input.items() if v is not None}}

        criteria = {
            "cpv_codes": list(options["cpvCodes"] or []),
            "countries": list(options["countries"] or []),
            "min_value_eur": int(options["minValueEur"] or 0),
            "keywords": list(options["keywords"] or []),
        }
        max_results = max(1, int(options["maxResults"] or 1))

        Actor.log.info(
            "Searching TED: cpv=%s countries=%s minValueEur=%s keywords=%s maxResults=%d"
            % (
                criteria["cpv_codes"],
                criteria["countries"],
                criteria["min_value_eur"],
                criteria["keywords"],
                max_results,
            )
        )

        charge_state = {"limit_reached": False, "charged": 0, "failures": 0}

        # Only a run billed per event can be charged. Under any other pricing
        # model the SDK ignores the charge, and that must not stop the search.
        pricing = Actor.get_charging_manager().get_pricing_info()
        is_pay_per_event = pricing.is_pay_per_event
        if not is_pay_per_event:
            Actor.log.info(
                "This run is not billed per event (pricing model: %s). "
                "Searching without charging." % pricing.pricing_model
            )

        # The HTTP calls to TED are synchronous and paced by a delay, so they
        # run in a worker thread to keep the Actor event loop responsive.
        # Filled by the client with the counts of this search, so the summary
        # row can say how many notices were read even when none matched.
        stats: dict = {"noticesRead": 0, "matchedNotices": 0, "duplicatesDropped": 0}

        notices = await asyncio.to_thread(
            collect_matching_notices,
            criteria=criteria,
            max_results=max_results,
            since_date=options["publishedSince"],
            log=Actor.log.info,
            stats=stats,
        )

        Actor.log.info(
            "%d notices read from TED, %d matching after filtering and dedupe."
            % (stats.get("noticesRead", 0), len(notices))
        )

        stored = 0
        for notice in notices:
            if is_pay_per_event and not await charge_one(charge_state):
                break
            await Actor.push_data(tag_notice(notice))
            stored += 1

        report = {
            "noticesRead": stats.get("noticesRead", 0),
            "matchedNotices": len(notices),
            "storedNotices": stored,
            "chargedEvents": charge_state["charged"],
            "chargeLimitReached": charge_state["limit_reached"],
            "chargeFailures": charge_state["failures"],
            "criteria": describe_criteria(criteria),
        }

        # A successful run always writes at least this row. A buyer with a
        # narrow CPV code gets zero matches often, and an empty dataset reads
        # as a broken Actor; the summary row says, inside the dataset, how many
        # notices were read and that the filters are what returned nothing.
        # It is pushed after the charging loop and charges nothing: the only
        # charged event, `tender-matched`, is charged per matching notice above.
        await Actor.push_data(build_summary_row(report))

        Actor.log.info(
            f"Wrote {stored + 1} row(s) to dataset "
            f"{Actor.configuration.default_dataset_id} "
            f"({stored} tender row(s) plus 1 '{SUMMARY_ROW_TYPE}' row)"
        )

        summary = {**report, "criteriaUsed": criteria}
        await Actor.set_value("SUMMARY", summary)

        if charge_state["limit_reached"]:
            Actor.log.info(
                "The run stopped early because it reached its pay-per-event "
                "charge limit. Raise the limit to receive more tenders."
            )
        Actor.log.info(
            "Finished: %d notices matched, %d stored, %d events charged."
            % (len(notices), stored, charge_state["charged"])
        )


if __name__ == "__main__":
    asyncio.run(main())
