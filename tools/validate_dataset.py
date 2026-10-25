"""Check the published dataset before it goes out.

Run:  python -I validate_dataset.py <dataset-dir>

The converter validates one provider file against its PDF. This validates the
dataset as a whole: that the index and the files it points at agree, and that
nothing in them would make the app show a wrong or silently empty schedule.

It is the last gate before publishing, so it fails loudly and says exactly what
is wrong. A hand-edit to a JSON file that breaks index agreement is caught here
rather than by a household looking at the wrong bin day.
"""

import datetime as dt
import json
import sys
from pathlib import Path

BIN_FRACTIONS = {"MKO", "EMB", "BIO", "PAP", "STE"}
SPECIAL_FRACTIONS = {"KOS", "NEV"}
ALL_FRACTIONS = BIN_FRACTIONS | SPECIAL_FRACTIONS
SUPPORTED_SCHEMA_VERSION = 1


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    root = Path(sys.argv[1])
    problems: list[str] = []

    index_path = root / "index.json"
    if not index_path.exists():
        print(f"FAIL: no index.json in {root}")
        return 1
    index = json.loads(index_path.read_text(encoding="utf-8"))

    if index.get("schemaVersion") != SUPPORTED_SCHEMA_VERSION:
        problems.append(f"index schemaVersion is {index.get('schemaVersion')}, expected {SUPPORTED_SCHEMA_VERSION}")

    entries = index.get("files", [])
    if not entries:
        problems.append("index lists no files")

    seen_keys: set[tuple[str, int]] = set()
    referenced: set[str] = set()

    for entry in entries:
        path = entry.get("path", "<missing path>")
        key = (entry.get("municipalityId"), entry.get("year"))
        if key in seen_keys:
            problems.append(f"{path}: duplicate index entry for {key}")
        seen_keys.add(key)
        referenced.add(path)

        file_path = root / path
        if not file_path.exists():
            problems.append(f"index points at {path}, which does not exist")
            continue

        provider = json.loads(file_path.read_text(encoding="utf-8"))

        # The index is what the app reads before downloading anything. If it
        # disagrees with the file, the app decides whether to fetch using facts
        # that turn out to be false.
        for field in ("municipalityId", "year", "version", "schemaVersion", "coversFrom", "coversTo"):
            in_index = entry.get(field)
            if field == "municipalityId":
                in_file = provider.get("municipality", {}).get("id")
            elif field == "year":
                in_file = int(provider.get("coversFrom", "0-")[:4]) or None
            else:
                in_file = provider.get(field)
            if in_index != in_file:
                problems.append(f"{path}: index says {field}={in_index!r}, file says {in_file!r}")

        covers_from = provider.get("coversFrom")
        covers_to = provider.get("coversTo")
        try:
            start, end = dt.date.fromisoformat(covers_from), dt.date.fromisoformat(covers_to)
        except (TypeError, ValueError):
            problems.append(f"{path}: coversFrom/coversTo are not both ISO dates")
            continue
        if end < start:
            problems.append(f"{path}: coverage ends ({end}) before it starts ({start})")

        zone_ids = {z.get("id") for z in provider.get("zones", [])}
        if not zone_ids:
            problems.append(f"{path}: declares no zones")

        colors = provider.get("provider", {}).get("colors", {})
        for code in colors:
            if code not in ALL_FRACTIONS:
                problems.append(f"{path}: colour for unknown fraction {code!r}")

        collections = provider.get("collections", [])
        if not collections and not provider.get("rules"):
            problems.append(f"{path}: has neither collections nor rules, so it can only ever answer empty")

        seen_pairs: set[tuple[str, str, str]] = set()
        bins_used: set[str] = set()
        for item in collections:
            date_text, code, zone = item.get("date"), item.get("fraction"), item.get("zoneId")
            if zone not in zone_ids:
                problems.append(f"{path}: collection on {date_text} in undeclared zone {zone!r}")
            if code not in ALL_FRACTIONS:
                problems.append(f"{path}: unknown fraction {code!r} on {date_text}")
                continue
            if code in BIN_FRACTIONS:
                bins_used.add(code)
            try:
                date = dt.date.fromisoformat(date_text)
            except (TypeError, ValueError):
                problems.append(f"{path}: {date_text!r} is not an ISO date")
                continue
            if not start <= date <= end:
                problems.append(f"{path}: {date} lies outside the file's own coverage {start}..{end}")
            pair = (zone, date_text, code)
            if pair in seen_pairs:
                problems.append(f"{path}: {code} appears twice on {date_text} in {zone}")
            seen_pairs.add(pair)

        # Colour is the app's only colour, and the UI is forbidden from inventing one.
        missing_colors = sorted(bins_used - set(colors))
        if missing_colors:
            problems.append(f"{path}: collects {missing_colors} but publishes no colour for them")

    orphans = sorted(
        p.name for p in root.glob("*.json")
        if p.name != "index.json" and p.name not in referenced
    )
    if orphans:
        problems.append(f"files present but not in the index, so no app will ever fetch them: {orphans}")

    if problems:
        print(f"FAIL: {len(problems)} problem(s) in {root}")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    total = sum(
        len(json.loads((root / e["path"]).read_text(encoding="utf-8"))["collections"])
        for e in entries
    )
    print(f"OK: {len(entries)} file(s), {total} collections, index and files agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
