# Kanta dataset

The published waste-collection calendars behind [Kanta](https://github.com/dedpolx3/Kanta),
which tells Slovenian households which bin goes out tomorrow.

This repository is public because the app reads these files with no account and no backend:
whatever URL the app can read, anyone can read. The app's own source lives in a separate,
private repository. Nothing here is secret — it is public information republished as data.

**Published at:** `https://dedpolx3.github.io/kanta-data/`
**Index:** `https://dedpolx3.github.io/kanta-data/index.json`

## Why this repository exists separately

A city publishes next year's calendar when it chooses, often between November and late
December. If calendars shipped only inside the app, every household would need an app-store
update to learn their January dates, and anyone who did not update would see a schedule that
simply stops. Publishing the data separately means a new calendar reaches people within days
and without an app release.

## Layout

```
index.json                  what files exist, with their coverage and version
<municipality>-<year>.json  one municipality, one year
tools/                      the converter and the validator; not published
```

Only `*.json` at the root is deployed. The tools stay in the repository.

## The shape of a file

Each file is self-contained: it repeats its provider block rather than referencing a shared
one, so a single download is enough to answer for one household, offline, for its whole
coverage period.

The two fields that matter most:

- **`coversFrom` / `coversTo`** — the span this file is authoritative for, both inclusive.
  Without them an empty result is ambiguous, and the app cannot tell "nothing is collected
  that week" from "we do not have next year yet". With them it can say
  *Urnik za 2027 še ni objavljen* instead of showing an empty list that reads as "no
  collections".
- **`version`** — a file replaces a held file of the same `(municipalityId, year)` only when
  its version is higher. Years are additive: publishing 2027 never disturbs 2026, which is
  what lets a household see December and January in one list with nothing special happening
  on New Year's Day.

Collections are explicit dates, because that is how Slovenian providers publish. A `rules`
array exists for the minority who publish patterns such as "every second Tuesday", and a file
may use either or both.

## Adding or updating a calendar

```bash
python -I tools/convert_gornja_radgona.py <provider.pdf> .
python -I tools/validate_dataset.py .
```

The converter refuses to write unless every day label in the source matches its real weekday,
no fraction code is left unmatched, and the per-fraction totals equal an independent count
taken from the PDF's raw text layer. The validator then checks the dataset as a whole: that
the index agrees with the files, that no collection falls outside its own coverage or into an
undeclared zone, that no fraction repeats on a date, and that every collected bin has a
published colour.

Both run again in CI, and **nothing deploys if either fails** — the previously published
dataset stays live instead.

Bump `version` in both the file and its index entry when you change a published calendar.
Leaving it unchanged means apps that already hold the file will ignore the new one.

## A note on provenance

Calendars are transcribed from providers' public PDFs and web pages. Only facts are taken —
dates, zones, fraction codes. No provider PDF, logo or branding is republished here. Each
file records its `source` and the `retrievedAt` date it was taken.
