"""Pure matching helpers for the TED (Tenders Electronic Daily) Actor.

No external dependencies. Every function here is pure: it reads its arguments
and returns a new value, without touching the network, the disk or globals.

Privacy rule (constitution, section 4 / LGPD): we only ever keep public,
non-personal fields of a notice. `normalize_notice` therefore uses an
ALLOW LIST of eight output keys. Anything else in the raw record - including
buyer-person, winner-person, winner-touchpoint-name, jury-member-name-lot,
participant-name-lot, first-name-ubo, contact points, e-mails and phones -
is dropped because it is simply never copied.
"""

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Allow list: the ONLY keys the Actor ever emits, and the raw aliases we accept
# for each of them. A raw key that is not listed here cannot reach the output.
# ---------------------------------------------------------------------------
ALLOWED_FIELDS: Dict[str, tuple] = {
    "notice_title": ("notice_title", "notice-title", "noticeTitle", "title"),
    "buyer_name": ("buyer_name", "buyer-name", "buyerName", "organisation-name-buyer"),
    "buyer_country": (
        "buyer_country",
        "buyer-country",
        "buyerCountry",
        "country",
        "place-of-performance-country",
    ),
    "cpv_codes": (
        "cpv_codes",
        "cpv-codes",
        "cpvCodes",
        "classification-cpv",
        "cpv",
    ),
    "total_value_eur": (
        "total_value_eur",
        "total-value-eur",
        "totalValueEur",
        "total-value",
        "notice-value-eur",
    ),
    "deadline": (
        "deadline",
        "deadline-receipt-tender-date-lot",
        "deadline-receipt-request",
        "deadlineReceiptRequest",
        "submission-deadline",
    ),
    "publication_number": (
        "publication_number",
        "publication-number",
        "publicationNumber",
        "ND",
    ),
    "announcement_url": (
        "announcement_url",
        "announcement-url",
        "announcementUrl",
        "links",
        "notice-url",
    ),
}

OUTPUT_KEYS = tuple(ALLOWED_FIELDS.keys())


def _first_present(raw: Dict[str, Any], aliases: tuple) -> Any:
    for alias in aliases:
        if alias in raw and raw[alias] is not None:
            return raw[alias]
    return None


def _as_text(value: Any) -> Optional[str]:
    """Flatten TED multilingual/multivalue fields into a plain string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("eng", "en", "ENG", "value", "text"):
            if key in value:
                return _as_text(value[key])
        for item in value.values():
            text = _as_text(item)
            if text:
                return text
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _as_text(item)
            if text:
                return text
        return None
    return str(value)


def _as_code_list(value: Any) -> List[str]:
    """Flatten a CPV field into a flat list of code strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        codes: List[str] = []
        for item in value.values():
            codes.extend(_as_code_list(item))
        return codes
    if isinstance(value, (list, tuple)):
        codes = []
        for item in value:
            codes.extend(_as_code_list(item))
        return codes
    return [str(value)]


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "eur", "EUR"):
            if key in value:
                return _as_float(value[key])
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = _as_float(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace(" ", "")
        if not text:
            return None
        # "1.234.567,89" -> "1234567.89"
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def normalize_notice(raw: dict) -> dict:
    """Return the eight public fields of a raw TED record, and nothing else.

    Built as an allow list: the output dict starts empty and only the eight
    keys of ALLOWED_FIELDS are ever written into it, so no personal field of
    the raw record can leak through.
    """
    if not isinstance(raw, dict):
        raw = {}

    notice: Dict[str, Any] = {}
    for field, aliases in ALLOWED_FIELDS.items():
        value = _first_present(raw, aliases)
        if field == "cpv_codes":
            notice[field] = _as_code_list(value)
        elif field == "total_value_eur":
            notice[field] = _as_float(value)
        else:
            notice[field] = _as_text(value)
    return notice


def cpv_matches(notice_cpvs: list, wanted: list) -> bool:
    """Hierarchical CPV match by prefix.

    A wanted code matches a notice code when the notice code sits at or below
    the wanted code in the CPV tree. Trailing zeros of the wanted code mark the
    level, so "45000000" matches "45213150", while "45213150" does not match
    "45000000".
    """
    if not wanted:
        return True
    if not notice_cpvs:
        return False

    for wanted_code in wanted:
        wanted_text = str(wanted_code or "").strip().split("-")[0]
        if not wanted_text:
            continue
        prefix = wanted_text.rstrip("0") or wanted_text[:1]
        for notice_code in notice_cpvs:
            notice_text = str(notice_code or "").strip().split("-")[0]
            if not notice_text:
                continue
            if notice_text == wanted_text or notice_text.startswith(prefix):
                return True
    return False


def matches(notice: dict, criteria: dict) -> bool:
    """True when the normalized notice satisfies every criterion given."""
    if not isinstance(notice, dict):
        return False
    criteria = criteria or {}

    wanted_cpvs = criteria.get("cpv_codes") or []
    if wanted_cpvs and not cpv_matches(notice.get("cpv_codes") or [], wanted_cpvs):
        return False

    countries = criteria.get("countries") or []
    if countries:
        country = (notice.get("buyer_country") or "").strip().upper()
        if country not in {str(c or "").strip().upper() for c in countries}:
            return False

    min_value = criteria.get("min_value_eur")
    if min_value:
        value = notice.get("total_value_eur")
        if value is None or value < float(min_value):
            return False

    keywords = criteria.get("keywords") or []
    if keywords:
        title = (notice.get("notice_title") or "").lower()
        if not any(str(word or "").lower() in title for word in keywords if str(word or "").strip()):
            return False

    return True


def dedupe(notices: list) -> list:
    """Drop repeats by publication_number, keeping the first, in order."""
    seen = set()
    unique = []
    for notice in notices or []:
        key = notice.get("publication_number") if isinstance(notice, dict) else None
        if key is None:
            unique.append(notice)
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(notice)
    return unique
