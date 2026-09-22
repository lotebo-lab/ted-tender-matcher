# EU Tenders Scraper: TED Public Procurement Notices by CPV Code, Country and Value

Run it on Apify Store: https://apify.com/lotebo-lab/ted-tender-matcher

Every working day Tenders Electronic Daily (TED), the official journal of European public procurement, publishes hundreds of contract notices. The handful that fit what your company actually sells is buried in the rest, and the TED website hands you a search form rather than a file you can work with.

This Actor takes the filters you already use when you look for work — the **CPV codes** you sell under, the **buyer countries** you serve, a **minimum contract value**, and optional **title keywords** — queries the public TED Search API, and returns **one dataset row per matching notice**, with eight public fields each: title, buyer organisation, buyer country, CPV codes, total value in euros, submission deadline, publication number and the link to the notice on ted.europa.eu.

**It returns no personal data at all.** No contact name, no e-mail, no telephone, no winner person, no jury member. That is not a setting you can turn off: the code is built as an allow list of eight fields, described in "No personal data comes out of this Actor" below.

**It does not tell you whether you can win, and it does not write a bid.** It filters and formats what TED published. What it leaves out is listed by name in "What this Actor does not do", and the things it genuinely cannot see are in "Limits of what it does cover". Read both before you buy.

## Who runs it, and when

Companies and consultants that bid for public contracts in the European Union, and the people who prepare their pipeline:

- **daily or weekly tender watch**, replacing a manual search on the TED site with a scheduled run that drops new notices into a dataset, a spreadsheet or your CRM;
- **filtering a wide CPV family down to the few notices that fit**, for example everything under `45000000` (construction works) but only in Germany and Portugal, only above EUR 200,000, and only where the title mentions a school;
- **feeding a bid/no-bid pipeline**, where each row carries the value and the deadline your team needs in order to decide quickly;
- **market sizing and competitor research**, when you want to know how many notices of a certain CPV family a country published in a period, with values;
- **building an internal alert**, because the output is JSON, CSV or Excel through the standard Apify dataset export, and the Apify scheduler can run it without anyone logging in.

The reader this output is built for is the person who has to decide, before lunch, which two of forty notices are worth opening. That is why every row carries `announcement_url`: the row is a filter, and the notice on ted.europa.eu is the truth.

## Where the data comes from

| what | value |
|---|---|
| source | Tenders Electronic Daily (TED), the EU supplement of public procurement notices |
| API | TED Search API, `POST https://api.ted.europa.eu/v3/notices/search` |
| authentication | none. "TED API allows anonymous access to all services manipulating published notices" (https://docs.ted.europa.eu/api/latest/index.html) |
| fields requested | the eight in the table below, from the official field list (https://docs.ted.europa.eu/ODS/latest/reuse/field-list.html) |
| reuse licence | "can be freely reused, for commercial or non-commercial purposes" (https://ted.europa.eu/en/legal-notice) |

You do **not** need a TED account, an EU Login or an API key to run this Actor. The key TED issues through EU Login covers notices that are **not yet published**, and this Actor never touches those; it reads the published journal, which is open.

The request is a `POST`. A `GET` on the same URL answers HTTP 405, which is written down in `src/ted_client.py` so nobody rebuilds the call the wrong way.

### The eight fields it asks TED for

These are the exact names sent in the `fields` array of the request body (`ALLOWED_API_FIELDS` in `src/ted_client.py`), and the column each one becomes in your dataset:

| field asked from TED | column in the dataset | what it holds |
|---|---|---|
| `notice-title` | `notice_title` | the title of the notice, as TED published it, in the language TED published it |
| `buyer-name` | `buyer_name` | the contracting organisation. An organisation, never a person |
| `buyer-country` | `buyer_country` | the buyer country, as a three letter code such as `DEU` |
| `classification-cpv` | `cpv_codes` | every CPV code attached to the notice, flattened into a list of strings |
| `total-value` | `total_value_eur` | the total value of the contract, as a number, when the notice carries one |
| `deadline-receipt-tender-date-lot` | `deadline` | the deadline for receipt of tenders, with the time zone TED published |
| `publication-number` | `publication_number` | the TED publication number, for example `00512345-2026` |
| `announcement-url` | `announcement_url` | the link to the notice page on ted.europa.eu |

The request is built by `build_search_body`, which filters whatever it is given against that list of eight. A caller cannot widen the request into another field, personal or not, even by mistake: an unknown name is dropped, and if nothing survives, the full list of eight is used.

## How the filtering works

There are two stages, on purpose.

**Stage one, sent to TED.** `build_expert_query` turns your CPV codes, your countries and `publishedSince` into a TED expert query, so the heavy narrowing happens on TED's side and the Actor downloads less:

```
(classification-cpv=45000000) AND (buyer-country=DEU OR buyer-country=PRT) AND publication-date>=2026-09-01
```

Several CPV codes are joined with `OR`, several countries are joined with `OR`, and the groups are joined with `AND`. With no filters at all the query is `*`, which means "everything TED published", and that is a lot: start narrow.

**Stage two, applied here, on the rows that came back** (`src/matching.py`). The value filter, the keyword filter and the hierarchical CPV rule are applied locally, so a change in TED's query language can never silently widen what you receive:

- **CPV, hierarchically.** CPV is a tree, and a wanted code matches any notice code at or below it. `45000000` matches `45213150` and `45214210`; `45213150` does **not** match `45000000`. The rule is prefix based: the trailing zeros of the code you ask for mark the level (`cpv_matches`).
- **Country**, compared upper case against `buyer_country`, exact match on the three letter code.
- **Minimum value**, a plain "greater than or equal". A notice published **without** a value cannot pass a minimum above zero, because there is no number to compare.
- **Keywords**, plain substring match on the title, ignoring case, `OR` between them. There is no stemming and no lemmatisation: `school` does not match `schools`.
- **Dedupe** by `publication_number`, keeping the first occurrence, preserving order.

Then the result is cut to `maxResults`.

### Paging, pace and failures

- One request at a time, up to 100 notices per page (`DEFAULT_PAGE_SIZE`), and TED refuses a page above 250.
- To leave room for the local filters, the Actor fetches wider than your `maxResults`: up to five times it, capped at 2,000 raw notices. Filtering then cuts it back down to `maxResults`.
- It stops at the first of: `maxResults` matches collected, an empty page, the total TED reports, a short page, or 100 pages.
- It waits 0.25 s between pages. TED publishes a ceiling of "700 requests in the last minute" and "3 concurrent downloads" for reusers (https://ted.europa.eu/en/simap/developers-corner-for-reusers); one request at a time with a quarter second between pages is an order of magnitude under that.
- Every call carries a 30 s timeout and a `User-Agent` that names the Actor, so TED can see who is calling.
- Transient failures — HTTP 408, 425, 429, 500, 502, 503, 504, and network errors such as a timeout or a reset — are retried up to 4 times with a wait that doubles from 2 s, capped at 60 s. A numeric `Retry-After` header from TED wins when it asks for a longer wait.
- **HTTP 400 is deliberately not retried.** A malformed query is our bug, and it must surface on the first attempt instead of being hidden behind four silent retries.

## Input

This is the input the Actor page is prefilled with, so you can run it once and read the shape of a row before you send your real filters:

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
| `cpvCodes` | array of strings | `[]` (empty, any category) | Common Procurement Vocabulary codes. Hierarchical: `45000000` also returns everything below it, such as `45213150`. Several codes are combined with `OR` |
| `countries` | array of strings | `[]` (empty, every country) | buyer country as a **three letter** code: `DEU`, `FRA`, `ESP`, `PRT`. A two letter code such as `DE` matches nothing and returns zero rows without an error |
| `minValueEur` | integer | `0` | drop notices whose total value is below this amount in euros. Minimum 0. A notice published with no value is dropped whenever this is above zero |
| `keywords` | array of strings | `[]` (filter skipped) | words looked for in the notice **title**, case insensitive, `OR` between them |
| `publishedSince` | string | empty (every published notice) | date in the form `YYYY-MM-DD`, to look only at notices published on or after it. The input schema rejects any other shape |
| `maxResults` | integer | `100` | largest number of matching notices stored in one run, from 1 to 5000. This is also what caps your bill, because the Actor charges per stored matching notice |

Every filter is optional. An empty input is valid and returns whatever TED published, which is why the sensible first run is one CPV family and one country you already follow.

## What comes out, field by field

The dataset holds two kinds of row, told apart by `rowType`: one **tender** row per matching notice, and exactly one **summary** row per run, written last. The names below are the keys the code writes, in `src/matching.py` and `src/summary_row.py`.

### The tender row

| field | type | what it holds |
|---|---|---|
| `rowType` | string | `tender` on every row of this kind |
| `notice_title` | string or null | the title as published. TED returns titles per language; the Actor takes the English text when the notice carries one, otherwise the first language available |
| `buyer_name` | string or null | the contracting organisation, under the same language rule |
| `buyer_country` | string or null | three letter country code of the buyer, for example `DEU` |
| `cpv_codes` | array of strings | every CPV code on the notice, flattened. A notice usually carries the main code plus one or more secondary ones |
| `total_value_eur` | number or null | the total value as a number. `null` when the notice published none. Text amounts such as `1.234.567,89` are parsed into `1234567.89` |
| `deadline` | string or null | the deadline for receipt of tenders, exactly as TED published it, time zone included, for example `2026-10-30T12:00:00+02:00` |
| `publication_number` | string or null | the TED publication number, also the key used to deduplicate inside a run |
| `announcement_url` | string or null | the notice page on ted.europa.eu. Always open it before preparing a bid |

A field TED did not fill comes back `null` (or an empty list for `cpv_codes`) rather than being missing, so a spreadsheet keeps its columns aligned.

### The summary row, one per run

An empty dataset is an ambiguous answer: "your filters matched nothing" and "the Actor broke" would look identical. So every successful run writes one last row, `rowType` = `summary`, that says which of the two happened, inside the dataset itself.

| field | type | what it holds |
|---|---|---|
| `rowType` | string | always `summary` on this row |
| `finishedAt` | string | when the run finished, ISO 8601 in UTC |
| `noticesRead` | integer | how many raw notices TED answered, before any local filter |
| `matchedNotices` | integer | how many of them passed CPV, country, value and keyword filtering, after dedupe |
| `storedNotices` | integer | how many tender rows this dataset actually holds |
| `chargedEvents` | integer | how many `tender-matched` events this run charged |
| `chargeLimitReached` | boolean | true when the run stopped early because your pay-per-event limit was spent, so part of the matches were not stored |
| `chargeFailures` | integer | charge calls that did not go through; the run finishes the work anyway |
| `criteria` | string | the filters of this run in one readable clause, for example `CPV 45000000; countries DEU, PRT; minimum value EUR 200000; keywords school` |
| `message` | string | the same counts in one plain sentence, written for a human reading the file later |

The `message` field distinguishes three cases in words, not just in numbers:

- **nothing read**: "No notice was read from TED for this search, so this run says nothing about whether a matching tender exists", plus the filters used;
- **read but nothing matched**: how many notices were read, that none matched, that the search itself worked, and which knob to widen (fewer CPV codes, more countries, a lower minimum value, fewer keywords);
- **matches found**: how many of how many matched, and how many were stored.

When nothing matched it also states that no tender event was charged, and when the run stopped on a charge limit it says so.

### The SUMMARY record in the key-value store

Every run also writes a `SUMMARY` record in the default key-value store with the same counts (`noticesRead`, `matchedNotices`, `storedNotices`, `chargedEvents`, `chargeLimitReached`, `chargeFailures`) plus `criteriaUsed`, the criteria as a structured object rather than a sentence. It is exposed on the run page as "Run summary".

### No personal data comes out of this Actor

TED does publish fields about natural persons. The official field list includes `buyer-person`, `winner-person`, `winner-touchpoint-name`, `jury-member-name-lot`, `participant-name-lot`, `first-name-ubo`, and organisation contact points carrying e-mail and telephone. Personal data is therefore reachable through this API, and this Actor is built so that none of it reaches your dataset:

- **the request is an allow list.** The `fields` array sent to TED can only contain the eight names listed above. A personal field is never even asked for;
- **the output is the same allow list, applied again.** `normalize_notice` starts from an empty dictionary and writes exactly those eight keys. A field that arrives in the answer and is not one of the eight is simply never copied, including a field TED may add in the future;
- **nothing in the input can widen it.** There is no "extra fields" option, no raw passthrough and no URL for you to supply.

This is tested, not asserted: `tests/test_ted_client.py` includes `test_body_never_asks_for_a_personal_field`, `test_fetch_never_sends_a_personal_field_name`, `test_pipeline_output_has_exactly_the_eight_public_keys` and `test_pipeline_drops_every_personal_field_of_the_source`, and `tests/test_matching.py` includes `test_normalize_drops_every_personal_field`, which checks both the key names and the values.

## Example output

The rows below come from an end-to-end run of `src/main.py` recorded in our build log, `logs/corrida-local-2026-09-20.log`. In that run the `POST` to `https://api.ted.europa.eu/v3/notices/search` is answered from `tests/fixtures/ted_search_page1.json`, because the machine that builds this Actor has no route to TED. **The values are illustrative; the column names are the real ones.** The `logs/` folder is excluded by the `.gitignore` of this repository, so the file lives in our workspace, not in the published tree. That log was also recorded before the summary row existed, so the rows below carry no `rowType` field; both the tag and the summary row are covered by `tests/test_summary_row.py`.

The fixture holds 5 notices, the filters above kept 2.

```json
{"announcement_url": "https://ted.europa.eu/en/notice/-/detail/00512345-2026", "buyer_country": "DEU", "buyer_name": "Stadt Dortmund", "cpv_codes": ["45214210", "45000000"], "deadline": "2026-10-30T12:00:00+02:00", "notice_title": "Construction of a primary school in Dortmund", "publication_number": "00512345-2026", "total_value_eur": 2400000.0}
```

```json
{"announcement_url": "https://ted.europa.eu/en/notice/-/detail/00512590-2026", "buyer_country": "PRT", "buyer_name": "Municipio de Braga", "cpv_codes": ["45214100"], "deadline": "2026-11-05T15:00:00+00:00", "notice_title": "Construction of a school canteen in Braga", "publication_number": "00512590-2026", "total_value_eur": 1250000.0}
```

The three notices that were dropped show the filters working: a road refurbishment in Aveiro at EUR 850,000 (no keyword match), and two others below the CPV or the country filter.

The `SUMMARY` record of the same run:

```json
{
  "matchedNotices": 2,
  "storedNotices": 2,
  "chargedEvents": 0,
  "chargeLimitReached": false,
  "chargeFailures": 0,
  "criteria": {
    "cpv_codes": ["45000000"],
    "countries": ["DEU", "PRT"],
    "min_value_eur": 200000,
    "keywords": ["school"]
  }
}
```

`chargedEvents` is 0 there because a local run is not billed per event; the Actor detects that and searches without charging.

The last three lines of the same log are the privacy check:

```
Personal fields in the source response: ['buyer-person', 'contact-email', 'contact-telephone', 'first-name-ubo', 'jury-member-name-lot', 'winner-person', 'winner-touchpoint-name']
Personal field names in the dataset: []
Personal field values found in the dataset: []
```

The source answer carried seven personal fields. None of them, by name or by value, reached the dataset.

## Price

Pay per event, one event, exactly as declared in `.actor/actor.json`:

| event | price | when it is charged |
|---|---|---|
| `tender-matched` | US$ 0.10 | once for each matching tender notice stored in the dataset. A run that finds nothing is not charged |

That is the whole price list of this Actor. There is no start fee and no subscription set by us. A run that reads 500 notices and stores 3 matches costs US$ 0.30. A run that reads 500 notices and matches none costs nothing in Actor events. Apify charges its own platform usage of the run on top of this, and that part is set by the platform, not by this Actor.

Two things follow from charging per **stored** match, and both are in your hands:

- **`maxResults` is your ceiling.** The Actor never stores more matches than that number, so it never charges more than `maxResults` events.
- **The pay-per-event limit stops the run, it does not lose your data.** If a run reaches the limit you set, it stops storing more tenders, keeps every tender already stored, writes the summary row with `chargeLimitReached: true`, and says so in the log.

The summary row itself charges nothing. Charging failures never kill a run: a charge call that does not answer within 5 seconds, or fails, is logged as a warning, counted in `chargeFailures`, and the search carries on.

## What this Actor does not do

- **It does not tell you whether you can or should bid.** No eligibility check, no scoring, no "chance of winning", no recommendation.
- **It does not write, submit or track a bid**, and it does not talk to any procurement portal on your behalf.
- **It does not return contact details of anyone.** No name, no e-mail, no telephone, for the buyer, the winner, a jury member or a beneficial owner.
- **It does not download the tender documents**, the annexes, the technical specifications or any attachment. It returns the link to the notice; the documents stay on ted.europa.eu.
- **It does not translate.** The title and the buyer name come in the language TED published them, with English preferred when the notice carries it.
- **It does not cover national or regional procurement portals** that do not publish to TED. Contracts below the EU thresholds usually never reach TED at all.
- **It does not monitor over time by itself.** One run is one snapshot. Use the Apify scheduler to repeat it; there is no memory between runs, so the same notice matched in two runs comes back in both.
- **It does not enrich the notice with company data**, registry lookups, financials, or anything about the buyer beyond the name and country TED published.
- **It does not search the body of the notice.** Keywords hit the title only.

## Limits of what it does cover

- **Three letter country codes only.** `DE` instead of `DEU` matches nothing and returns zero rows **without an error**, which looks like an empty result rather than a mistake in the input. Check `noticesRead` in the summary row when a run surprises you.
- **A notice with no published value cannot pass a minimum value above zero.** Contract notices are frequently published without a value, so a high `minValueEur` silently removes a whole class of notices. Set it to 0 if you would rather see them and judge yourself.
- **No stemming and no plural forms in keywords.** `school` does not match `schools`, and `bau` does not match `Bauarbeiten` unless it appears as that substring. List the variants you want.
- **Language.** Titles are published in the language of the buyer. A keyword in English will not match a notice whose title only exists in Portuguese.
- **Dedupe is per run, by publication number.** A corrigendum or an updated notice with a different publication number is a different row. Across two runs, nothing is deduplicated.
- **The deadline is text, as published**, with its own time zone, and it is not normalised or validated. A deadline can also be extended by a corrigendum you will only see by opening the notice.
- **The value is a number, as published.** Where TED publishes the amount in another currency, this Actor does not convert it: the column is named `total_value_eur` because that is the field TED offers in euros, and no conversion is attempted here.
- **Paging has a ceiling**: 100 pages, and at most 2,000 raw notices read per run. A very wide query hits that ceiling instead of reading the whole journal.
- **Speed.** Pages are fetched one at a time with a pause between them, deliberately, to stay far inside the published rate limit of a free public service. A wide search takes minutes, not seconds.
- **This Actor is a filter, not a legal source.** The notice on ted.europa.eu is the document that counts. Always open `announcement_url` before acting on a row.

## Manners, rate limits and your responsibility

- **This Actor requests no URL that you supply.** It calls one documented endpoint, `https://api.ted.europa.eu/v3/notices/search`, and nothing else. There is no crawl: no site of yours and no site of a buyer is ever fetched, so there is no `robots.txt` for this Actor to consult on your behalf.
- **It respects the limits TED publishes for reusers**: one request at a time out of the three concurrent downloads allowed, a pause between pages, well under the 700 requests per minute ceiling (https://ted.europa.eu/en/simap/developers-corner-for-reusers).
- **It identifies itself** with a `User-Agent` naming the Actor on every request.
- **Reuse of TED content is allowed, including commercially**: the European Commission decision of 12/12/2011 states the data "can be freely reused, for commercial or non-commercial purposes", and that "reuse is allowed provided appropriate credit is given and any changes made are indicated" (https://ted.europa.eu/en/legal-notice). The content is under CC BY 4.0 and the metadata under CC0; the TED logo and the EU trademarks are not covered and are not used here.
- **Your use of the output is yours.** This Actor reformats a public source; it does not grant you any right beyond the ones the TED legal notice grants, and it is not endorsed by the Publications Office of the European Union.

## How this was checked

**What passes, today, offline.** The whole suite runs without network, with a fake transport injected in place of the HTTP client. Run from the Actor directory, on 2026-09-22:

| file | checks | what it covers |
|---|---|---|
| `tests/test_matching.py` | 28/28 | normalisation, the eight key allow list, hierarchical CPV, country, value, keywords, dedupe |
| `tests/test_ted_client.py` | 38/38 | request body, expert query, paging, retries and backoff, `Retry-After`, 400 not retried, and the privacy cases named above |
| `tests/test_schemas.py` | 78/78 | the four `.actor` JSON files parse, reference each other correctly, the pay-per-event block holds exactly one event `tender-matched` at US$ 0.10 matching the name the charging code uses, and every field named in a schema really exists in a row the code pushes |
| `tests/test_summary_row.py` | 27/27 | the summary row is written on every successful run, carries its declared fields, and tells the three cases apart in words |

That is 171 checks, all green. `tests/test_ted_client.py` prints `no network used: every case injected a fake transport` before its result, which is the point: these tests prove the logic, not the API.

**What has run on the Apify platform.** This Actor is public, and it has run in the cloud. One of those runs, on build 0.2.3, was a deliberate probe with a filter chosen so that nothing could match (CPV `03111700`, country `MLT`): it finished `SUCCEEDED` and its dataset held exactly one row, the summary row, reading "No notice was read from TED for this search, so this run says nothing about whether a matching tender exists", with `chargedEvents` 0. That proves the Actor starts, calls TED, survives an empty answer, writes the summary row and charges nothing when nothing matches.

**What has not been proven, stated plainly.** No run of this Actor has yet parsed a **real TED notice body**. The field names are taken from the official TED field list (https://docs.ted.europa.eu/ODS/latest/reuse/field-list.html) and every test feeds them from a fixture we wrote, `tests/fixtures/ted_search_page1.json`. If the live answer nests a field differently from the fixture, the affected column can come back `null` while the row itself is still written. The allow list means the failure mode is an empty column, never an unexpected or personal one. If you see a column that is empty when the notice on ted.europa.eu clearly shows the value, tell us through the Issues tab on the Actor page, with the publication number: that is the single most useful thing a first user can send us, and it gets fixed.

We would rather lose a sale than let you find this out after paying.

## Where this Actor runs, and what is in this repository

This repository holds the source code. The Actor itself runs on the Apify platform, and its page is https://apify.com/lotebo-lab/ted-tender-matcher, where you can read the input schema, the price per event and the example input, and start it without installing anything.

| path | what it is |
|---|---|
| `src/main.py` | the Actor entry point: reads the input, runs the search, charges one event per stored match, writes the summary row and the `SUMMARY` record |
| `src/ted_client.py` | the TED Search API client: request body, expert query, paging, pace, retries. No Apify dependency, so it is testable on its own |
| `src/matching.py` | pure functions: the eight field allow list, hierarchical CPV matching, filters, dedupe. No network, no disk, no globals |
| `src/summary_row.py` | the run summary row and its sentence |
| `.actor/actor.json` | the Actor definition, including the single pay-per-event charge event |
| `.actor/input_schema.json` | the input form you see on the Actor page |
| `.actor/dataset_schema.json` | the dataset fields and the table view |
| `.actor/output_schema.json` | the run output links: the dataset items and the `SUMMARY` record |
| `tests/` | the offline suite described above, plus `tests/fixtures/ted_search_page1.json` |
| `Dockerfile` | built on the official `apify/actor-python:3.13` image; the build fails early if the source does not compile |

## Source and credit

Data from Tenders Electronic Daily (TED), Publications Office of the European Union. This Actor reformats that data, filters it and adds no field of its own to a notice; it is not an official document and it is not endorsed by the Publications Office. Credit and the indication of changes are given here as the TED legal notice requires (https://ted.europa.eu/en/legal-notice).

This Actor was built with the help of AI. Every module has an offline automated test in `tests/`, and the counts above were run on 2026-09-22.
