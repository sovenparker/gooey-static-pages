#!/usr/bin/env python3
"""Pull /research content from the Google Sheet into the static page.

The page does NOT read the sheet at load time. This script is the explicit
refresh: run it after editing the sheet, check the diff, commit, push.

    python3 scripts/sync_research.py            # fetch, write, report
    python3 scripts/sync_research.py --dry-run  # show what would change

It writes two things, both committed to the repo:
  * research/data/{Papers,Articles,Profiles}.csv  — readable snapshot, so the
    git diff shows exactly which content changed
  * the <script type="application/json" id="research-data"> block inside
    research/index.html — what the page actually renders
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SHEET_ID = "1sk_HB3sJgYer0shLibe8MvNRmRmdPzWE48U_GxXmIxY"

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "research" / "index.html"
DATA_DIR = ROOT / "research" / "data"

# tab name -> (json key, required columns, optional columns)
#
# `slug` is what joins a Papers row to its built page. It is optional: when the
# cell is blank the page falls back to deriving a slug from the title, which is
# how this worked before the column existed. Fill it in and the title becomes
# free to change without breaking the link.
TABS = {
    "Papers": ("papers", ["type", "title", "venue", "date", "note", "note_url",
                          "logo", "blurb", "authors", "pdf_url", "tags", "hidden"],
               ["slug"]),
    "Articles": ("articles", ["column", "title", "meta", "url", "hidden"], []),
    "Profiles": ("profiles", ["name", "role", "photo", "link_label",
                              "link_url", "hidden"], []),
}

ISLAND_RE = re.compile(
    r'(<script type="application/json" id="research-data">\n)(.*?)(\n\s*</script>)',
    re.S,
)


def fetch_tab(tab: str) -> str:
    url = (
        f"https://docs.google.com/spreadsheets/d/{urllib.parse.quote(SHEET_ID)}"
        f"/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(tab)}"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"ERROR: tab {tab!r} returned HTTP {e.code}.\n"
            "  Check the tab name, and that the sheet is shared "
            "'Anyone with the link = Viewer'."
        ) from e
    except urllib.error.URLError as e:
        raise SystemExit(f"ERROR: could not reach Google for tab {tab!r}: {e.reason}") from e


def rows_from_csv(text: str, tab: str, required: list[str]) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise SystemExit(f"ERROR: tab {tab!r} came back empty.")

    header = [h.strip().lower() for h in rows[0]]
    missing = [c for c in required if c not in header]
    if missing:
        raise SystemExit(
            f"ERROR: tab {tab!r} is missing column(s): {', '.join(missing)}\n"
            f"  got: {', '.join(header)}"
        )

    out = []
    for r in rows[1:]:
        out.append({h: (r[i].strip() if i < len(r) else "")
                    for i, h in enumerate(header)})
    return out


def warn_blank_cells(tab: str, rows: list[dict]) -> list[str]:
    """Google blanks cells that don't match a column's inferred type — the
    classic case is a text date like '25-27 November' in a date column."""
    warnings = []
    watch = {"Papers": ["type", "title", "venue", "date"],
             "Articles": ["column", "title"],
             "Profiles": ["name", "role"]}.get(tab, [])
    for n, row in enumerate(rows, start=2):
        for col in watch:
            if not row.get(col, "").strip():
                label = row.get("title") or row.get("name") or f"row {n}"
                warnings.append(f"  {tab} row {n} ({label[:40]}): {col!r} is empty")
    return warnings


def write_csv(path: pathlib.Path, rows: list[dict], header: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({h: r.get(h, "") for h in header})
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, but write nothing")
    args = ap.parse_args()

    if not PAGE.exists():
        raise SystemExit(f"ERROR: {PAGE} not found — run this from the repo.")

    payload: dict[str, object] = {}
    csv_text: dict[str, str] = {}
    all_warnings: list[str] = []

    print(f"Sheet {SHEET_ID}")
    for tab, (key, required, optional) in TABS.items():
        rows = rows_from_csv(fetch_tab(tab), tab, required)
        present = [c for c in optional if any(c in r for r in rows)]
        missing_optional = [c for c in optional if c not in present]
        for r in rows:
            for c in optional:
                r.setdefault(c, "")
        payload[key] = rows
        csv_text[tab] = write_csv(DATA_DIR / f"{tab}.csv", rows, required + present)
        all_warnings += warn_blank_cells(tab, rows)
        note = f"  (no {', '.join(missing_optional)} column yet)" if missing_optional else ""
        print(f"  {tab:9} {len(rows):3} rows{note}")

    if all_warnings:
        print("\nWARNING: empty cells that probably should have content:")
        print("\n".join(all_warnings))
        print("  Usually a column formatted as a date/number instead of text.")
        print("  Fix: select the column -> Format -> Number -> Plain text, retype the cell.\n")

    # Keep the old timestamp when the content is unchanged, so re-running the
    # sync on an unchanged sheet produces no diff at all.
    page = PAGE.read_text()
    previous = ISLAND_RE.search(page)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if previous:
        try:
            old_payload = json.loads(previous.group(2))
            old_stamp = old_payload.pop("_meta", {}).get("generatedAt")
            if old_stamp and old_payload == payload:
                stamp = old_stamp
        except json.JSONDecodeError:
            pass

    payload["_meta"] = {"generatedAt": stamp, "sheetId": SHEET_ID}

    # '<' escaped so the JSON can never break out of the <script> block
    blob = json.dumps(payload, ensure_ascii=False, indent=0).replace("<", "\\u003c")
    if not previous:
        raise SystemExit('ERROR: could not find the id="research-data" block in index.html')
    new_page = ISLAND_RE.sub(lambda m: m.group(1) + blob + m.group(3), page, count=1)

    changed = []
    if new_page != page:
        changed.append(str(PAGE.relative_to(ROOT)))
    for tab in TABS:
        path = DATA_DIR / f"{tab}.csv"
        if not path.exists() or path.read_text() != csv_text[tab]:
            changed.append(str(path.relative_to(ROOT)))

    if args.dry_run:
        print("\n--dry-run: nothing written."
              + ("\nWould change:\n  " + "\n  ".join(changed) if changed else
                 "\nAlready up to date."))
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for tab in TABS:
        (DATA_DIR / f"{tab}.csv").write_text(csv_text[tab])
    PAGE.write_text(new_page)

    if changed:
        print("\nUpdated:\n  " + "\n  ".join(changed))
        print("\nReview with `git diff`, then commit and push to deploy.")
    else:
        print("\nAlready up to date — nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
