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
from datetime import datetime
from datetime import datetime, timezone

SHEET_ID = "1sk_HB3sJgYer0shLibe8MvNRmRmdPzWE48U_GxXmIxY"

# Articles columns that are pulled from a feed instead of the Sheet. Rows for
# these columns in the Articles tab are ignored — delete them, they are noise.
# If a feed cannot be reached the Sheet's rows for that column are used instead,
# so a network blip degrades to the old behaviour rather than emptying a card.
FEEDS = {
    "Gooey Blog": {
        "kind": "llmstxt",
        "url": "https://blog.gooey.ai/llms.txt",
        # supplies a date per post; llms.txt has titles and links but no dates
        "dates": "https://blog.gooey.ai/sitemap-pages.xml",
        # the index entry lists itself; it is not a post
        "skip": ("gooey.ai-updates-and-blog",),
        "limit": 3,
    },
    "Medium": {
        "kind": "rss",
        "url": "https://medium.com/feed/@seanb",
        "limit": 3,
    },
}

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


def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "gooey-static-pages/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _month_year(iso: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %Z",
                "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(iso, fmt).strftime("%B %Y")
        except ValueError:
            continue
    return ""


def fetch_llmstxt(cfg: dict) -> list[dict]:
    """blog.gooey.ai publishes llms.txt: '- [Title](url.md): description',
    newest first. Dates come from the sitemap."""
    entries = []
    for line in _get(cfg["url"]).splitlines():
        m = re.match(r"\s*-\s*\[(.+?)\]\((https?://[^)\s]+)\)\s*(?::\s*(.*))?$", line)
        if not m:
            continue
        title, url, desc = m.group(1).strip(), m.group(2), (m.group(3) or "").strip()
        url = re.sub(r"\.md$", "", url)
        if any(sk in url for sk in cfg.get("skip", ())):
            continue
        entries.append({"title": title, "url": url, "desc": desc})

    dates = {}
    if cfg.get("dates"):
        try:
            xml = _get(cfg["dates"])
            for loc, mod in re.findall(r"<loc>(.*?)</loc>\s*(?:<priority>.*?</priority>\s*)?"
                                       r"<lastmod>(.*?)</lastmod>", xml, re.S):
                dates[loc.strip()] = _month_year(mod.strip())
        except Exception:
            pass

    out = []
    for e in entries[: cfg["limit"]]:
        # the description carries the real period ("Product Updates August
        # 2026"); the sitemap only knows when a post was last touched
        out.append({"title": e["title"], "url": e["url"],
                    "meta": e["desc"] or dates.get(e["url"], ""), "hidden": ""})
    return out


def fetch_rss(cfg: dict) -> list[dict]:
    xml = _get(cfg["url"])
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[: cfg["limit"]]:
        def tag(name):
            m = re.search(rf"<{name}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", item, re.S)
            return (m.group(1).strip() if m else "")
        title, link = tag("title"), tag("link")
        if not (title and link):
            continue
        out.append({"title": title, "url": link,
                    "meta": _month_year(tag("pubDate")), "hidden": ""})
    return out


def fetch_feed(column: str, cfg: dict) -> tuple[list[dict], str]:
    """Returns (rows, error). On error the caller keeps the Sheet's rows."""
    try:
        rows = {"llmstxt": fetch_llmstxt, "rss": fetch_rss}[cfg["kind"]](cfg)
    except Exception as e:  # network, parse, anything
        return [], f"{type(e).__name__}: {e}"
    if not rows:
        return [], "feed had no usable entries"
    for r in rows:
        r["column"] = column
    return rows, ""


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


def merge_feeds(sheet_rows: list[dict], warnings: list[str]) -> tuple[list[dict], str]:
    """Replace the feed-backed columns with live entries, keeping the Sheet's
    column order. Sheet rows for a feed column are dropped — unless the feed
    could not be fetched, in which case they are kept as a fallback."""
    order, seen = [], set()
    for r in sheet_rows:
        c = r.get("column", "")
        if c not in seen:
            seen.add(c)
            order.append(c)
    for c in FEEDS:
        if c not in seen:
            order.append(c)

    fetched, notes = {}, []
    for column, cfg in FEEDS.items():
        rows, err = fetch_feed(column, cfg)
        if err:
            warnings.append(f"  {column}: feed unavailable ({err}) — kept the Sheet's rows")
            notes.append(f"{column} from Sheet")
        else:
            fetched[column] = rows
            notes.append(f"{column} from feed ({len(rows)})")

    out = []
    for column in order:
        if column in fetched:
            out += fetched[column]
        else:
            out += [r for r in sheet_rows if r.get("column") == column]
    return out, "  [" + ", ".join(notes) + "]" if notes else ""


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
        note = f"  (no {', '.join(missing_optional)} column yet)" if missing_optional else ""

        if tab == "Articles" and FEEDS:
            rows, feed_note = merge_feeds(rows, all_warnings)
            note = (note + feed_note) if feed_note else note

        payload[key] = rows
        csv_text[tab] = write_csv(DATA_DIR / f"{tab}.csv", rows, required + present)
        all_warnings += warn_blank_cells(tab, rows)
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
