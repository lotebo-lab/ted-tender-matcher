# EU Tenders Scraper: TED Public Procurement Notices

Run it on Apify Store: https://apify.com/lotebo-lab/ted-tender-matcher

Every working day Tenders Electronic Daily publishes hundreds of public contract notices, and the handful that fit what your company sells are buried in the rest.

Give it the CPV codes you sell under, the buyer countries you serve and a minimum contract value. It searches TED and returns one row per matching notice with eight public fields: title, buyer, country, CPV codes, value, deadline, publication number and the link to the notice.

## Input

```json
{
  "cpvCodes": ["45000000"],
  "countries": ["DEU", "PRT"],
  "minValueEur": 200000,
  "keywords": ["school"],
  "maxResults": 50
}
```

| field | type | default | notes |
|---|---|---|---|
| `cpvCodes` | array of strings | `[]` | CPV codes; a parent code such as `45000000` matches its children |
| `countries` | array of strings | `[]` | buyer country as a **three letter** code: `DEU`, `PRT`, `FRA`. A two letter code such as `DE` matches nothing |
| `minValueEur` | integer | 0 | notices below this value are dropped |
| `keywords` | array of strings | `[]` | matched against the notice title, case insensitive |
| `publishedSince` | string | — | date, to limit how far back the search goes |
| `maxResults` | integer | 100 | 1 to 5000 |

All filters are optional, but an empty input returns whatever TED published, which is a lot. Start with one CPV code and one country you already follow.

## Example output

The rows below are copied from `logs/corrida-local-2026-09-20.log` in this repository, lines 20 to 42. That is an end-to-end run of `src/main.py` in which the POST to `https://api.ted.europa.eu/v3/notices/search` is answered from `tests/fixtures/ted_search_page1.json`, because the build sandbox has no route to TED. The search returned 5 notices and 2 survived the filters above. No run against the live TED API exists yet.

The two matching notices, one row each (lines 20 and 21):

```json
{"announcement_url": "https://ted.europa.eu/en/notice/-/detail/00512345-2026", "buyer_country": "DEU", "buyer_name": "Stadt Dortmund", "cpv_codes": ["45214210", "45000000"], "deadline": "2026-10-30T12:00:00+02:00", "notice_title": "Construction of a primary school in Dortmund", "publication_number": "00512345-2026", "total_value_eur": 2400000.0}
```

```json
{"announcement_url": "https://ted.europa.eu/en/notice/-/detail/00512590-2026", "buyer_country": "PRT", "buyer_name": "Municipio de Braga", "cpv_codes": ["45214100"], "deadline": "2026-11-05T15:00:00+00:00", "notice_title": "Construction of a school canteen in Braga", "publication_number": "00512590-2026", "total_value_eur": 1250000.0}
```

The run summary, which repeats the criteria that produced those rows (line 23):

```json
{
  "matchedNotices": 2,
  "storedNotices": 2,
  "chargedEvents": 0,
  "criteria": {
    "cpv_codes": ["45000000"],
    "countries": ["DEU", "PRT"],
    "min_value_eur": 200000,
    "keywords": ["school"]
  }
}
```

The same log records, at lines 43 to 45, that the source answer carried seven personal fields (`contact-email`, `winner-person`, `jury-member-name-lot` and others) and that none of them, by name or by value, reached the dataset.

## Limits

- `countries` only accepts the three letter code. `DE` instead of `DEU` returns zero rows with no error, which looks like an empty result rather than a mistake in the input.
- A notice with no value published cannot pass a `minValueEur` above zero.
- `keywords` match the title only, not the body of the notice, and there is no stemming: `school` does not match `schools`.
- Notices are deduplicated by publication number inside a run.
- The deadline comes from the notice as published, with its own timezone. Always open `announcement_url` before preparing a bid.
- TED data is reusable: the European Commission decision of 12/12/2011 states it "can be freely reused, for commercial or non-commercial purposes" (https://ted.europa.eu/en/legal-notice), and the search API needs no key (https://docs.ted.europa.eu/api/latest/index.html).
- Charging: one event only, `tender-matched`, at US$ 0.10 per matching notice stored, as declared in `.actor/actor.json`. A run that finds nothing matching is not charged. There is no start fee charged by this Actor.

## What this Actor does not do

- It does not return contact details of anyone: no name, no e-mail, no telephone, for buyer, winner or jury member.
- It does not download the tender documents, the annexes or the specification files.
- It does not translate the notice; the title comes as TED published it.
- It does not write, submit or track a bid, and it does not score your chance of winning.
- It does not cover national procurement portals that do not publish to TED.
- It does not tell you whether you are eligible to bid.
