"""Convert Komunala Radgona's printed 2026 calendar into a Kanta provider file.

Run:  python -I convert_gornja_radgona.py <path-to-pdf> <output-dir>

Why this is not a text extraction
---------------------------------
`pdftotext -layout` and `pdftotext -table` disagree about which month column a
fraction code belongs to: `-layout` puts EMB next to 1 January, `-table` leaves that
cell empty and assigns codes to April and June on the same row. Both report the same
96 codes. The day-to-code mapping simply is not recoverable from the text layer, and a
wrong pickup date is this project's worst possible bug.

So this reads the PDF content streams directly and uses the real x/y of every string.
A day label sits at its month column's left edge; its fraction codes sit 25 to 95
points to the right at the same y. That is exact, not inferred.

Two traps worth knowing about, both of which produced silently wrong output first:

1.  Page 1 is a `/Contents` array: its content is split across several stream objects
    that begin mid-operator. Parsed separately, the graphics state is wrong and codes
    land at coordinates that belong to no page. They must be concatenated first.
2.  Python's `str.strip()` treats bytes 0x1C-0x1F as whitespace, and the C-caron in
    "CE" (cetrtek) arrives as 0x1F. Stripping before normalising deleted it, which
    silently dropped all 27 Thursday cells on a page.

Everything it produces is checked before it is written. Any failed check aborts.
"""

import datetime as dt
import json
import re
import sys
import zlib
from collections import Counter

TOKEN = re.compile(rb"""
    \((?:\\.|[^\\()])*\) | <[0-9A-Fa-f\s]*> | /[^\s/\[\]<>()]+
  | [\[\]] | [-+]?\d*\.?\d+ | [A-Za-z'"*]+
""", re.VERBOSE)

MONTHS_PAGE1 = {"Januar": 1, "Februar": 2, "Marec": 3, "April": 4, "Maj": 5, "Junij": 6}
MONTHS_PAGE2 = {"Julij": 7, "Avgust": 8, "September": 9, "Oktober": 10,
                "November": 11, "December": 12}
WEEKDAYS = {"PO": 0, "TO": 1, "SR": 2, "CE": 3, "PE": 4, "SO": 5, "NE": 6}
CODES = {"MKO", "BIO", "EMB", "PAP", "STE", "NEV", "KOS"}

# Counted independently from the raw text layer (every occurrence of each code in the
# document, minus the two legends). Reconciling against these is the check that the
# coordinate matching neither invented nor lost a collection.
EXPECTED_TOTALS = {"MKO": 21, "BIO": 37, "EMB": 18, "PAP": 13, "STE": 6, "NEV": 1}
DAY_RE = re.compile(r"^([A-Z]{2}),\s*(\d+)\.$")


def unescape(token: bytes) -> str:
    body, out, i = token[1:-1], bytearray(), 0
    simple = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}
    while i < len(body):
        ch = body[i]
        if ch == 0x5C and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in simple:
                out.append(simple[nxt]); i += 2; continue
            if 0x30 <= nxt <= 0x37:
                j, digits = i + 1, b""
                while j < len(body) and len(digits) < 3 and 0x30 <= body[j] <= 0x37:
                    digits += bytes([body[j]]); j += 1
                out.append(int(digits, 8) & 0xFF); i = j; continue
            out.append(nxt); i += 2; continue
        out.append(ch); i += 1
    return bytes(out).decode("cp1252", errors="replace")


def matmul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C,
            c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def positioned_text(stream: bytes):
    """Every shown string with its page coordinates."""
    out, stack, operands = [], [], []
    ctm = tm = tlm = (1, 0, 0, 1, 0, 0)
    leading = 0.0
    for match in TOKEN.finditer(stream):
        token = match.group()
        if token[:1] in b"(<\\/" or token in (b"[", b"]"):
            operands.append(token); continue
        try:
            operands.append(float(token)); continue
        except ValueError:
            pass
        op = token.decode("latin-1")
        nums = [o for o in operands if isinstance(o, float)]
        if op == "q":
            stack.append(ctm)
        elif op == "Q" and stack:
            ctm = stack.pop()
        elif op == "cm" and len(nums) >= 6:
            ctm = matmul(tuple(nums[-6:]), ctm)
        elif op == "BT":
            tm = tlm = (1, 0, 0, 1, 0, 0)
        elif op == "Tm" and len(nums) >= 6:
            tm = tlm = tuple(nums[-6:])
        elif op == "TL" and nums:
            leading = nums[-1]
        elif op in ("Td", "TD") and len(nums) >= 2:
            if op == "TD":
                leading = -nums[-1]
            tlm = matmul((1, 0, 0, 1, nums[-2], nums[-1]), tlm); tm = tlm
        elif op == "T*":
            tlm = matmul((1, 0, 0, 1, 0, -leading), tlm); tm = tlm
        elif op in ("Tj", "TJ", "'", '"'):
            if op in ("'", '"'):
                tlm = matmul((1, 0, 0, 1, 0, -leading), tlm); tm = tlm
            text = "".join(unescape(o) for o in operands
                           if isinstance(o, bytes) and o[:1] == b"(")
            if text.strip():
                placed = matmul(tm, ctm)
                out.append((round(placed[4], 2), round(placed[5], 2), text))
        operands = []
    return out


def clean(text: str) -> str:
    """Normalise before stripping: 0x1F is the C-caron and str.strip() eats it."""
    return "".join("C" if ch == "\x1f" else ch for ch in text).strip()


def content_streams(pdf: bytes):
    streams = []
    for match in re.finditer(rb"stream\r?\n", pdf):
        start = match.end()
        end = pdf.find(b"endstream", start)
        if end == -1:
            continue
        try:
            inflated = zlib.decompress(pdf[start:end])
        except zlib.error:
            continue
        if b"Tj" in inflated or b"TJ" in inflated:
            streams.append(inflated)
    return streams


def read_page(blob: bytes, months: dict):
    items = [(x, y, clean(t)) for x, y, t in positioned_text(blob)]
    columns = sorted((x, months[t]) for x, y, t in items if t in months)
    if len(columns) != 6:
        raise SystemExit(f"expected 6 month columns, found {len(columns)}")

    def column_of(x):
        found = None
        for cx, month in columns:
            if x >= cx - 6:
                found = (cx, month)
        return found

    cells, wrong_weekday = {}, []
    for x, y, text in items:
        m = DAY_RE.match(text)
        if not m:
            continue
        column = column_of(x)
        if not column:
            continue
        cx, month = column
        if x > cx + 25 or m.group(1) not in WEEKDAYS:
            continue
        date = dt.date(2026, month, int(m.group(2)))
        if date.weekday() != WEEKDAYS[m.group(1)]:
            wrong_weekday.append((date, m.group(1)))
        cells[(month, round(y))] = date

    collections, orphans = [], []
    for x, y, text in items:
        # "BIO + " with "pranje" beneath it means the bin is washed that day.
        washed = text.startswith("BIO +")
        code = "BIO" if washed else text
        if code not in CODES:
            continue
        column = column_of(x)
        if not column:
            continue
        cx, month = column
        if not 25 < x - cx < 95:          # the legend sits far right of every column
            continue
        date = (cells.get((month, round(y)))
                or cells.get((month, round(y) + 1))
                or cells.get((month, round(y) - 1)))
        if date is None:
            orphans.append((x, y, text))
        else:
            collections.append((date, code, ["PRANJE"] if washed else []))
    return cells, collections, wrong_weekday, orphans


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    pdf = open(sys.argv[1], "rb").read()
    streams = content_streams(pdf)
    if len(streams) < 12:
        raise SystemExit(f"expected at least 12 content streams, found {len(streams)}")

    # Page 1's content is an array of fragments that begin mid-operator; joining them
    # is what makes its graphics state, and therefore its coordinates, correct.
    pages = [(b"\n".join(streams[0:11]), MONTHS_PAGE1), (streams[11], MONTHS_PAGE2)]

    all_cells, all_collections, bad, orphans = 0, [], [], []
    for blob, months in pages:
        cells, collections, wrong, orphan = read_page(blob, months)
        all_cells += len(cells)
        all_collections += collections
        bad += wrong
        orphans += orphan

    problems = []
    if all_cells != 365:
        problems.append(f"day cells: {all_cells}, expected 365 (181 + 184)")
    if bad:
        problems.append(f"{len(bad)} day labels disagree with the real 2026 weekday: {bad[:5]}")
    if orphans:
        problems.append(f"{len(orphans)} fraction codes matched no day cell: {orphans[:5]}")
    totals = Counter(code for _, code, _ in all_collections)
    if dict(totals) != EXPECTED_TOTALS:
        problems.append(f"totals {dict(sorted(totals.items()))} != text-layer count {EXPECTED_TOTALS}")
    seen = Counter((date, code) for date, code, _ in all_collections)
    if any(n > 1 for n in seen.values()):
        problems.append(f"duplicates: {[k for k, n in seen.items() if n > 1][:5]}")
    if problems:
        raise SystemExit("REFUSING TO WRITE:\n  - " + "\n  - ".join(problems))

    zone = "gornja-radgona"
    provider_file = {
        "schemaVersion": 1,
        "version": 1,
        "provider": {
            "id": "komunala-radgona",
            "name": "Komunala Radgona d.o.o.",
            "website": "https://www.komunala-radgona.si",
            "phone": "080 17 27",
            "colors": {"MKO": "#3B3F44", "BIO": "#7B4A2A", "EMB": "#F2C230",
                       "PAP": "#2F6FB5", "STE": "#23744A"},
        },
        "municipality": {"id": zone, "name": "Obcina Gornja Radgona", "postcode": "9250"},
        "zones": [{"id": zone, "name": "Celotna obcina"}],
        "coversFrom": "2026-01-01",
        "coversTo": "2026-12-31",
        "source": "https://www.komunala-radgona.si",
        "retrievedAt": "2026-10-25",
        "collections": [
            dict({"zoneId": zone, "date": date.isoformat(), "fraction": code},
                 **({"flags": flags} if flags else {}))
            for date, code, flags in sorted(all_collections, key=lambda c: (c[0], c[1]))
        ],
        "rules": [],
        "exceptions": [],
        "onCall": [{"fraction": "KOS", "phone": "080 17 27", "note": "Odvoz na klic."}],
    }

    index = {
        "schemaVersion": 1,
        "files": [{
            "municipalityId": zone,
            "municipalityName": "Obcina Gornja Radgona",
            "postcode": "9250",
            "year": 2026,
            "path": "gornja-radgona-2026.json",
            "version": 1,
            "schemaVersion": 1,
            "coversFrom": "2026-01-01",
            "coversTo": "2026-12-31",
        }],
    }

    out = sys.argv[2].rstrip("/\\")
    for name, payload in (("gornja-radgona-2026.json", provider_file), ("index.json", index)):
        with open(f"{out}/{name}", "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    print(f"day cells 365/365, weekday mismatches 0, orphans 0")
    print(f"collections {len(all_collections)}: {dict(sorted(totals.items()))}")
    print(f"wrote {out}/gornja-radgona-2026.json and {out}/index.json")


if __name__ == "__main__":
    main()
