#!/usr/bin/env python3
"""Build the /research paper pages from papers_content.py.

Each paper becomes a standalone page at research/papers/<slug>/index.html —
served at /research/papers/<slug>/ — laid out to match the "Shaping AI Paper"
artboard in the Gooey.AI design canvas.

Every asset path in a paper page is written relative to research/. The gooey.ai
proxy pins <base> to the top-level folder (verified: /sovereignty serves
<base href="…/sovereignty/">), so that is what relative paths resolve against in
production. Served directly — the raw CDN, or a local preview — no base is
injected, so each page carries its own <base href="../../"> fallback *after* the
GOOEY-BASE-HREF comment. Only the first <base> in a document counts, so the
proxy's wins when present and the fallback applies when it is not.

    python3 scripts/build_papers.py

It also rewrites the PAPER_PAGES manifest inside research/index.html, which is
what makes a row in the papers list clickable. A paper with no hosted PDF gets
no page and no link.

Assets it expects (committed, not generated here):
    research/pdf/<slug>.pdf              the hosted paper
    research/papers/<slug>/fig-N.jpg     figures pulled out of that PDF
"""

from __future__ import annotations

import html
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from papers_content import PAPERS  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "research"
INDEX = RESEARCH / "index.html"

MANIFEST_RE = re.compile(
    r"(/\* BEGIN PAPER PAGES[^\n]*\n\s*const PAPER_PAGES = )(.*?)(;\n\s*/\* END PAPER PAGES \*/)",
    re.S,
)


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def rich(s: str) -> str:
    """Escape, then re-enable the few inline marks used in the source text:
    *emphasis*, **strong**, and [label](url) links."""
    out = esc(s)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                 r'<a href="\2" target="_blank" rel="noopener">\1</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    return out


def slug_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def slugify(title: str) -> str:
    """Must stay in step with slugify() in research/index.html."""
    return slug_id(title.replace("&", " and "))


# --------------------------------------------------------------------------- #
# blocks
# --------------------------------------------------------------------------- #
def render_block(block, slug: str) -> str:
    kind = block[0]
    if kind == "p":
        return f"<p>{rich(block[1])}</p>"
    if kind == "h3":
        return f"<h3>{rich(block[1])}</h3>"
    if kind == "ul":
        items = "".join(f"<li>{rich(i)}</li>" for i in block[1])
        return f"<ul>{items}</ul>"
    if kind == "fig":
        _, n, caption = block
        return (
            f'<figure>'
            f'<img src="papers/{slug}/fig-{n}.jpg" alt="{esc(caption)}" loading="lazy" />'
            f"<figcaption>{rich(caption)}</figcaption>"
            f"</figure>"
        )
    if kind == "q":
        _, quote, who = block
        attrib = f'<cite>{rich(who)}</cite>' if who else ""
        return f"<blockquote><p>{rich(quote)}</p>{attrib}</blockquote>"
    raise ValueError(f"unknown block kind: {kind!r}")


def render_refs(items) -> str:
    lis = "".join(
        f'<li><span class="n">[{n}]</span><span>{rich(i)}</span></li>'
        for n, i in enumerate(items, 1)
    )
    return f"<ol>{lis}</ol>"


def render_section(sec, slug: str) -> str:
    sid = sec.get("id") or slug_id(sec["title"])
    num = sec.get("num")
    label = f'<span class="secnum">{esc(num)}</span>' if num else ""
    cls = sec.get("cls", "")
    if "refs" in cls:
        body = "\n        ".join(
            render_refs(b[1]) if b[0] == "ul" else render_block(b, slug)
            for b in sec["blocks"]
        )
    else:
        body = "\n        ".join(render_block(b, slug) for b in sec["blocks"])
    cls_attr = ' class="{}"'.format(cls) if cls else ""
    heading = rich(sec["title"])
    return (
        f'      <section id="{sid}"{cls_attr}>\n'
        f"        <h2>{label}{heading}</h2>\n"
        f"        {body}\n"
        f"      </section>"
    )


def nav_entries(paper) -> list[tuple[str, str]]:
    out = [("abstract", "Abstract")]
    for sec in paper["sections"]:
        sid = sec.get("id") or slug_id(sec["title"])
        label = (f"{sec['num']} " if sec.get("num") else "") + sec["title"]
        out.append((sid, label))
    return out


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
STYLE = """
      @font-face {
        font-family: "Domine";
        src:
          url("assets/fonts/Domine-VariableFont_wght.ttf")
            format("truetype-variations"),
          url("assets/fonts/Domine-VariableFont_wght.ttf") format("truetype");
        font-weight: 400 700;
        font-style: normal;
        font-display: swap;
      }
      :root {
        --gy-ink: rgb(10, 16, 33);
        --gy-ink-muted: rgb(75, 88, 122);
        --gy-ink-meta: rgb(99, 99, 99);
        --gy-white: rgb(255, 255, 255);
        --gy-surface-100: rgb(247, 245, 242);
        --gy-line: rgb(217, 217, 217);
        --gy-line-soft: rgb(230, 230, 230);
        --gy-teal-700: #00998a;
        --gy-font-sans:
          "Inter", system-ui, -apple-system, "Segoe UI", Roboto, Arial,
          sans-serif;
        --gy-font-serif:
          "Domine", "Source Serif Pro", Georgia, "Times New Roman", serif;
      }
      *,
      *::before,
      *::after {
        box-sizing: border-box;
      }
      html {
        scroll-behavior: smooth;
      }
      html,
      body {
        margin: 0;
        padding: 0;
      }
      body {
        background: var(--gy-white);
        color: var(--gy-ink);
        font-family: var(--gy-font-serif);
        font-weight: 400;
        -webkit-font-smoothing: antialiased;
      }
      img {
        max-width: 100%;
        display: block;
      }
      a {
        color: var(--gy-teal-700);
        text-decoration: none;
      }
      a:hover {
        text-decoration: underline;
      }

      /* ---------- top bar ---------- */
      .topbar {
        border-bottom: 1px solid var(--gy-line-soft);
        background: var(--gy-white);
        position: sticky;
        top: 0;
        z-index: 5;
      }
      .topbar-inner {
        max-width: 1180px;
        margin: 0 auto;
        padding: 14px clamp(20px, 4vw, 40px);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        font-family: var(--gy-font-sans);
        font-size: 14px;
      }
      .back {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 7px 12px;
        margin-left: -12px;
        border-radius: 8px;
        color: var(--gy-ink);
        font-weight: 500;
        background: transparent;
      }
      .back:hover {
        background: var(--gy-surface-100);
        text-decoration: none;
      }
      .back i {
        font-size: 12px;
      }
      .topbar-right {
        display: flex;
        align-items: center;
        gap: 8px;
        color: var(--gy-ink-meta);
      }
      .topbar-right .sep {
        color: var(--gy-line);
      }
      .topbar-right .pdf {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 7px 12px;
        margin-right: -12px;
        border-radius: 8px;
        font-size: 13px;
        font-weight: 500;
        color: var(--gy-ink-muted);
      }
      .topbar-right .pdf:hover {
        background: var(--gy-surface-100);
        text-decoration: none;
      }

      /* ---------- layout ---------- */
      .shell {
        max-width: 1180px;
        margin: 0 auto;
        padding: clamp(32px, 5vw, 60px) clamp(20px, 4vw, 40px) 120px;
        display: grid;
        grid-template-columns: 212px minmax(0, 1fr);
        gap: clamp(28px, 4vw, 56px);
        align-items: start;
      }
      nav.toc {
        position: sticky;
        top: 88px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        font-family: var(--gy-font-sans);
        font-size: 13px;
        line-height: 140%;
        border-right: 1px solid var(--gy-line-soft);
        padding-right: 20px;
      }
      nav.toc .toc-label {
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--gy-ink-meta);
      }
      nav.toc a {
        color: var(--gy-ink);
        text-wrap: pretty;
      }
      nav.toc a.active {
        color: var(--gy-teal-700);
      }
      article {
        max-width: 760px;
        font-size: 17px;
        line-height: 172%;
      }

      /* ---------- header ---------- */
      article > header {
        display: flex;
        flex-direction: column;
        gap: 24px;
        margin-bottom: clamp(28px, 4vw, 40px);
      }
      article > header h1 {
        font-size: clamp(32px, 4.4vw, 40px);
        line-height: 122%;
        font-weight: 600;
        margin: 0;
        letter-spacing: -0.01em;
        text-wrap: pretty;
      }
      .authors {
        display: flex;
        flex-wrap: wrap;
        gap: 8px 28px;
        font-family: var(--gy-font-sans);
        font-size: 14px;
        line-height: 150%;
      }
      .authors > div {
        display: flex;
        flex-direction: column;
      }
      .authors .affil {
        color: var(--gy-ink-meta);
      }

      /* ---------- abstract ---------- */
      .abstract {
        background: var(--gy-surface-100);
        border: 1px solid var(--gy-line-soft);
        border-radius: 16px;
        padding: clamp(20px, 3vw, 28px);
        display: flex;
        flex-direction: column;
        gap: 14px;
        margin-bottom: clamp(28px, 4vw, 40px);
      }
      .abstract h2 {
        font-family: var(--gy-font-sans);
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--gy-ink-meta);
        margin: 0;
      }
      .abstract p {
        margin: 0;
        font-size: 16px;
        line-height: 170%;
        text-wrap: pretty;
      }
      .abstract-meta {
        display: flex;
        flex-direction: column;
        gap: 10px;
        padding-top: 14px;
        border-top: 1px solid var(--gy-line-soft);
        font-family: var(--gy-font-sans);
        font-size: 14px;
        line-height: 160%;
      }
      .abstract-meta .k {
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--gy-ink-meta);
        font-size: 12px;
        font-weight: 500;
        margin-right: 6px;
      }
      .abstract-meta .v {
        color: var(--gy-teal-700);
      }

      /* ---------- body ---------- */
      article section {
        display: flex;
        flex-direction: column;
        gap: 18px;
        margin-bottom: clamp(32px, 4vw, 44px);
      }
      article section h2 {
        font-size: 24px;
        line-height: 130%;
        font-weight: 600;
        margin: 0;
        text-wrap: pretty;
      }
      article section h2 .secnum {
        color: var(--gy-ink-meta);
        font-family: var(--gy-font-sans);
        font-size: 18px;
        margin-right: 12px;
      }
      article section h3 {
        font-size: 19px;
        line-height: 135%;
        font-weight: 600;
        margin: 6px 0 -4px;
        text-wrap: pretty;
      }
      article p {
        margin: 0;
        text-wrap: pretty;
      }
      article ul {
        margin: 0;
        padding-left: 22px;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      article li {
        text-wrap: pretty;
      }
      figure {
        margin: 10px 0;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      figure img {
        width: 100%;
        height: auto;
        border-radius: 12px;
        border: 1px solid var(--gy-line-soft);
      }
      figcaption {
        font-family: var(--gy-font-sans);
        font-size: 13px;
        line-height: 150%;
        color: var(--gy-ink-muted);
      }
      blockquote {
        margin: 0;
        display: flex;
        flex-direction: column;
        gap: 12px;
        background: var(--gy-surface-100);
        border: 1px solid var(--gy-line-soft);
        border-radius: 16px;
        padding: clamp(20px, 3vw, 24px);
      }
      blockquote p {
        margin: 0;
      }
      blockquote cite {
        font-family: var(--gy-font-sans);
        font-size: 13px;
        font-style: normal;
        color: var(--gy-ink-meta);
      }

      /* sections that close the paper */
      article section.tight {
        gap: 14px;
      }
      article section.tight h2,
      article section.ruled h2 {
        font-size: 20px;
      }
      article section.ruled {
        padding-top: clamp(24px, 3vw, 32px);
        border-top: 1px solid var(--gy-line-soft);
      }
      article section.ruled ol {
        margin: 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: 14px;
        font-size: 15px;
        line-height: 165%;
      }
      article section.ruled ol li {
        display: flex;
        gap: 10px;
      }
      article section.ruled ol li .n {
        color: var(--gy-ink-meta);
        font-family: var(--gy-font-sans);
        font-size: 13px;
        flex: none;
        min-width: 22px;
      }

      @media (max-width: 900px) {
        .shell {
          grid-template-columns: minmax(0, 1fr);
        }
        nav.toc {
          position: static;
          border-right: 0;
          border-bottom: 1px solid var(--gy-line-soft);
          padding-right: 0;
          padding-bottom: 20px;
        }
        article {
          font-size: 16px;
        }
      }
"""

PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <!-- GOOEY-BASE-HREF -->
    <base href="../../" />
    <title>{title_esc} — Gooey.AI Research</title>
    <meta name="description" content="{desc}" />
    <meta property="og:type" content="article" />
    <meta property="og:title" content="{title_esc} — Gooey.AI Research" />
    <meta property="og:description" content="{desc}" />
    <meta property="og:site_name" content="Gooey.AI" />
    <meta name="twitter:card" content="summary_large_image" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
    />
    <script
      src="https://kit.fontawesome.com/8af9787bd5.js"
      crossorigin="anonymous"
    ></script>
    <style>{style}    </style>
  </head>

  <body>
    <div class="topbar">
      <div class="topbar-inner">
        <a class="back" href="index.html"
          ><i class="fa-regular fa-arrow-left" aria-hidden="true"></i
          ><span>Gooey.AI Research</span></a
        >
        <div class="topbar-right">
          <span>{status}</span>
          <span class="sep">/</span>
          <a class="pdf" href="pdf/{slug}.pdf" target="_blank" rel="noopener"
            ><i class="fa-regular fa-file-pdf" aria-hidden="true"></i
            ><span>PDF</span></a
          >
        </div>
      </div>
    </div>

    <div class="shell">
      <nav class="toc">
        <div class="toc-label">Contents</div>
{nav}
      </nav>

      <article>
        <header>
          <h1>{title_esc}</h1>
          <div class="authors">
{authors}
          </div>
        </header>

        <section class="abstract" id="abstract">
          <h2>Abstract</h2>
          <p>{abstract}</p>
{abstract_meta}
        </section>

{sections}
      </article>
    </div>

    <script>
      /* highlight the contents entry for the section in view */
      const links = [...document.querySelectorAll("nav.toc a")];
      const byId = new Map(
        links.map((a) => [a.getAttribute("href").slice(1), a]),
      );
      const seen = new Set();
      const io = new IntersectionObserver(
        (entries) => {{
          entries.forEach((e) => {{
            if (e.isIntersecting) seen.add(e.target.id);
            else seen.delete(e.target.id);
          }});
          links.forEach((a) =>
            a.classList.toggle(
              "active",
              seen.has(a.getAttribute("href").slice(1)),
            ),
          );
        }},
        {{ rootMargin: "-88px 0px -70% 0px" }},
      );
      document
        .querySelectorAll("article section[id]")
        .forEach((s) => io.observe(s));
    </script>
  </body>
</html>
"""


def build_page(paper) -> str:
    slug = paper["slug"]

    nav = "\n".join(
        f'        <a href="#{sid}">{esc(label)}</a>' for sid, label in nav_entries(paper)
    )

    authors = "\n".join(
        "            <div><span>{n}</span><span class=\"affil\">{a}</span>{e}</div>".format(
            n=esc(name),
            a=esc(affil),
            e=(
                f'<a href="mailto:{esc(email)}">{esc(email)}</a>'
                if email
                else ""
            ),
        )
        for name, affil, email in paper["authors"]
    )

    meta_rows = []
    for key, vals in paper.get("meta", []):
        joined = "; ".join(vals) if isinstance(vals, list) else vals
        meta_rows.append(
            f'          <div><span class="k">{esc(key)}</span>'
            f'<span class="v">{rich(joined)}</span></div>'
        )
    abstract_meta = (
        '          <div class="abstract-meta">\n'
        + "\n".join(meta_rows)
        + "\n          </div>"
        if meta_rows
        else ""
    )

    sections = "\n\n".join(render_section(s, slug) for s in paper["sections"])

    desc = re.sub(r"\s+", " ", paper["abstract"])[:180].rsplit(" ", 1)[0] + "…"

    return PAGE.format(
        title_esc=esc(paper["title"]),
        desc=esc(desc),
        style=STYLE,
        status=esc(paper.get("status", "Preprint")),
        slug=slug,
        nav=nav,
        authors=authors,
        abstract=rich(paper["abstract"]),
        abstract_meta=abstract_meta,
        sections=sections,
    )


def main() -> int:
    if not INDEX.exists():
        raise SystemExit(f"ERROR: {INDEX} not found — run this from the repo.")

    manifest = {}
    written = []
    for paper in PAPERS:
        slug = paper["slug"]
        pdf = RESEARCH / "pdf" / f"{slug}.pdf"
        if not pdf.exists():
            print(f"  SKIP {slug}: no research/pdf/{slug}.pdf")
            continue
        missing = [
            b[1]
            for s in paper["sections"]
            for b in s["blocks"]
            if b[0] == "fig"
            and not (RESEARCH / "papers" / slug / f"fig-{b[1]}.jpg").exists()
        ]
        if missing:
            raise SystemExit(f"ERROR: {slug} references missing figures: {missing}")

        out_dir = RESEARCH / "papers" / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(build_page(paper))
        written.append(f"papers/{slug}/index.html")
        # keyed on slug — the Sheet's `slug` column, or a slug derived from the
        # title when that cell is blank. Titles can then be reworded freely.
        manifest[slug] = {
            "page": f"papers/{slug}/",
            "pdf": f"pdf/{slug}.pdf",
        }
        derived = slugify(paper["listed_as"])
        if derived != slug:
            raise SystemExit(
                f"ERROR: {slug!r} does not match the slug derived from its "
                f"listed_as title ({derived!r}).\n"
                "  Either rename the slug, or put the slug in the Sheet's "
                "`slug` column for that row."
            )

    page = INDEX.read_text()
    if not MANIFEST_RE.search(page):
        raise SystemExit('ERROR: could not find the PAPER_PAGES block in index.html')
    blob = json.dumps(manifest, ensure_ascii=False, indent=8, sort_keys=True)
    blob = blob.replace("<", "\\u003c")
    INDEX.write_text(MANIFEST_RE.sub(lambda m: m.group(1) + blob + m.group(3), page, count=1))

    print("Built:\n  " + "\n  ".join(written))
    print(f"\nLinked {len(manifest)} paper(s) from the list; "
          f"{len(PAPERS) - len(manifest)} skipped for want of a PDF.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
