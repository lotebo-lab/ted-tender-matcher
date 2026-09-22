"""Hand-rolled test runner for src/matching.py.

pytest is not installed in .venv and pip has no network inside the sandbox,
so this file runs its own cases and prints "N/N passed" on the last line,
exiting with code 1 when any case fails.

Run with:
    ../../.venv/bin/python tests/test_matching.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from matching import (  # noqa: E402
    OUTPUT_KEYS,
    cpv_matches,
    dedupe,
    matches,
    normalize_notice,
)

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def notice(**kwargs):
    base = {
        "notice_title": "Construction of a school",
        "buyer_name": "Ville de Lyon",
        "buyer_country": "FRA",
        "cpv_codes": ["45213150"],
        "total_value_eur": 500000.0,
        "deadline": "2026-11-30T12:00:00",
        "publication_number": "123456-2026",
        "announcement_url": "https://ted.europa.eu/notice/123456-2026",
    }
    base.update(kwargs)
    return base


# --------------------------------------------------------------------------
# CPV hierarchy
# --------------------------------------------------------------------------
@case
def test_cpv_parent_matches_child():
    assert cpv_matches(["45213150"], ["45000000"]) is True


@case
def test_cpv_child_does_not_match_parent():
    assert cpv_matches(["45000000"], ["45213150"]) is False


@case
def test_cpv_exact_match():
    assert cpv_matches(["45213150"], ["45213150"]) is True


@case
def test_cpv_unrelated_branch():
    assert cpv_matches(["45213150"], ["72000000"]) is False


@case
def test_cpv_empty_wanted_accepts_everything():
    assert cpv_matches(["72000000"], []) is True


@case
def test_cpv_ignores_check_digit_suffix():
    assert cpv_matches(["45213150-9"], ["45000000-7"]) is True


@case
def test_matches_uses_cpv_hierarchy():
    assert matches(notice(), {"cpv_codes": ["45000000"]}) is True
    assert matches(notice(cpv_codes=["45000000"]), {"cpv_codes": ["45213150"]}) is False


# --------------------------------------------------------------------------
# Country filter
# --------------------------------------------------------------------------
@case
def test_country_filter_keeps_wanted_country():
    assert matches(notice(), {"countries": ["FRA", "DEU"]}) is True


@case
def test_country_filter_drops_other_country():
    assert matches(notice(buyer_country="ESP"), {"countries": ["FRA", "DEU"]}) is False


@case
def test_country_filter_is_case_insensitive():
    assert matches(notice(buyer_country="fra"), {"countries": ["FRA"]}) is True


@case
def test_country_filter_drops_missing_country():
    assert matches(notice(buyer_country=None), {"countries": ["FRA"]}) is False


# --------------------------------------------------------------------------
# Minimum value
# --------------------------------------------------------------------------
@case
def test_min_value_keeps_value_above():
    assert matches(notice(total_value_eur=500000.0), {"min_value_eur": 100000}) is True


@case
def test_min_value_drops_value_below():
    assert matches(notice(total_value_eur=50000.0), {"min_value_eur": 100000}) is False


@case
def test_min_value_drops_missing_value():
    assert matches(notice(total_value_eur=None), {"min_value_eur": 100000}) is False


@case
def test_min_value_zero_keeps_missing_value():
    assert matches(notice(total_value_eur=None), {"min_value_eur": 0}) is True


# --------------------------------------------------------------------------
# Keywords
# --------------------------------------------------------------------------
@case
def test_keyword_is_case_insensitive():
    assert matches(notice(notice_title="CONSTRUCTION of a School"), {"keywords": ["school"]}) is True
    assert matches(notice(notice_title="construction of a school"), {"keywords": ["SCHOOL"]}) is True


@case
def test_keyword_any_word_matches():
    assert matches(notice(), {"keywords": ["hospital", "school"]}) is True


@case
def test_keyword_no_word_matches():
    assert matches(notice(), {"keywords": ["hospital", "bridge"]}) is False


@case
def test_keyword_does_not_look_at_buyer_name():
    assert matches(notice(notice_title="Road works"), {"keywords": ["Lyon"]}) is False


@case
def test_all_criteria_together():
    criteria = {
        "cpv_codes": ["45000000"],
        "countries": ["FRA"],
        "min_value_eur": 100000,
        "keywords": ["school"],
    }
    assert matches(notice(), criteria) is True
    assert matches(notice(total_value_eur=1000.0), criteria) is False


@case
def test_empty_criteria_keeps_notice():
    assert matches(notice(), {}) is True


# --------------------------------------------------------------------------
# Dedupe
# --------------------------------------------------------------------------
@case
def test_dedupe_by_publication_number_keeps_first_in_order():
    items = [
        notice(publication_number="1-2026", notice_title="first"),
        notice(publication_number="2-2026", notice_title="second"),
        notice(publication_number="1-2026", notice_title="first again"),
        notice(publication_number="3-2026", notice_title="third"),
    ]
    result = dedupe(items)
    assert [n["publication_number"] for n in result] == ["1-2026", "2-2026", "3-2026"]
    assert result[0]["notice_title"] == "first"


@case
def test_dedupe_empty_list():
    assert dedupe([]) == []


# --------------------------------------------------------------------------
# Normalization and the privacy allow list
# --------------------------------------------------------------------------
@case
def test_normalize_returns_exactly_eight_keys():
    result = normalize_notice({"notice-title": "Road works"})
    assert sorted(result.keys()) == sorted(OUTPUT_KEYS)
    assert len(result) == 8


@case
def test_normalize_reads_ted_field_names():
    raw = {
        "notice-title": {"eng": "Construction of a school"},
        "organisation-name-buyer": {"eng": "Ville de Lyon"},
        "buyer-country": "FRA",
        "classification-cpv": ["45213150-9", "45000000-7"],
        "total-value": "1.250.000,50",
        "deadline-receipt-request": "2026-11-30T12:00:00+01:00",
        "publication-number": "123456-2026",
        "notice-url": "https://ted.europa.eu/notice/123456-2026",
    }
    result = normalize_notice(raw)
    assert result["notice_title"] == "Construction of a school"
    assert result["buyer_name"] == "Ville de Lyon"
    assert result["buyer_country"] == "FRA"
    assert result["cpv_codes"] == ["45213150-9", "45000000-7"]
    assert result["total_value_eur"] == 1250000.50
    assert result["deadline"] == "2026-11-30T12:00:00+01:00"
    assert result["publication_number"] == "123456-2026"
    assert result["announcement_url"] == "https://ted.europa.eu/notice/123456-2026"


@case
def test_normalize_missing_fields_become_none_or_empty_list():
    result = normalize_notice({})
    assert result["notice_title"] is None
    assert result["total_value_eur"] is None
    assert result["deadline"] is None
    assert result["cpv_codes"] == []


@case
def test_normalize_drops_every_personal_field():
    """Decisive case: personal keys and their values must not survive."""
    raw = {
        "notice-title": "Construction of a school",
        "organisation-name-buyer": "Ville de Lyon",
        "buyer-country": "FRA",
        "classification-cpv": ["45213150"],
        "total-value": 500000,
        "deadline-receipt-request": "2026-11-30T12:00:00",
        "publication-number": "123456-2026",
        "notice-url": "https://ted.europa.eu/notice/123456-2026",
        # everything below is personal data and must be dropped
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
    result = normalize_notice(raw)

    forbidden_keys = [
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
    for key in forbidden_keys:
        assert key not in result, "leaked key %s" % key

    # no output key may even smell personal
    for key in result:
        lowered = key.lower().replace("_", "-")
        for banned in ("person", "ubo", "contact", "email", "phone", "first-name"):
            assert banned not in lowered, "output key %s contains %s" % (key, banned)

    forbidden_values = [
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
        "+33 4 72 11 11 11",
    ]
    blob = repr(result)
    for value in forbidden_values:
        assert value not in blob, "leaked value %s in %s" % (value, blob)

    # and the public fields did survive
    assert result["notice_title"] == "Construction of a school"
    assert result["publication_number"] == "123456-2026"
    assert sorted(result.keys()) == sorted(OUTPUT_KEYS)


@case
def test_normalize_output_feeds_matches_and_dedupe():
    raw_records = [
        {
            "notice-title": "School renovation",
            "buyer-country": "FRA",
            "classification-cpv": ["45213150"],
            "total-value": 300000,
            "publication-number": "1-2026",
            "buyer-person": "Marie Dupont",
        },
        {
            "notice-title": "School renovation",
            "buyer-country": "FRA",
            "classification-cpv": ["45213150"],
            "total-value": 300000,
            "publication-number": "1-2026",
            "winner-person": "Joao Silva",
        },
        {
            "notice-title": "Software licences",
            "buyer-country": "DEU",
            "classification-cpv": ["72000000"],
            "total-value": 900000,
            "publication-number": "2-2026",
        },
    ]
    normalized = dedupe([normalize_notice(r) for r in raw_records])
    assert len(normalized) == 2
    kept = [n for n in normalized if matches(n, {"cpv_codes": ["45000000"], "countries": ["FRA"]})]
    assert len(kept) == 1
    assert kept[0]["publication_number"] == "1-2026"
    assert "Marie Dupont" not in repr(normalized)
    assert "Joao Silva" not in repr(normalized)


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
    print("%d/%d passed" % (passed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
