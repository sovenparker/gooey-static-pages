# gooey-static-pages

Static pages hosted on **Cloudflare Pages** (project: `gooey-static-pages`) and served through gooey.ai via a proxy (`serve_static_file`).

Each top-level folder maps to a URL path:

```
gooey-static-pages/        ← repo root = the site root that gets deployed
├── sovereignty/           ← this folder name = the URL /sovereignty
│   ├── index.html         ← the html page for /sovereignty
│   ├── globe.js
│   ├── globe-grid.bin
│   ├── art.js
│   ├── assets/ ...
│   └── images/ ...
└── research/              ← this folder name = the URL /research
    ├── index.html         ← the html page for /research
    ├── data/              ← content snapshot pulled from the Sheet
    ├── pdf/               ← the hosted paper PDFs
    ├── papers/            ← one folder per paper
    │   └── <slug>/
    │       ├── index.html ← /research/papers/<slug>/  (generated)
    │       └── fig-N.jpg  ← figures extracted from that paper's PDF
    ├── assets/ ...
    └── images/ ...
```

## 📤 How to update the `/sovereignty` page

This repo is connected to **Cloudflare Pages**, so **pushing to GitHub deploys the site automatically**. No manual upload and no gooey.ai server deploy needed.

### 1. Edit the files

Edit the files under `sovereignty/`. Keep these in mind so the page keeps working when served through gooey.ai (the current page already does this — don't break it):

- **Required:** keep the `<!-- GOOEY-BASE-HREF -->` comment in `<head>`. The proxy swaps it for the right CDN `<base>` tag — remove it and images/fonts/the globe break on gooey.ai.
- **Required:** use **relative** asset paths (`assets/x.png`, `images/y.png`) — never `/static/...` or `/sovereignty/...`. The proxy rewrites relative paths to the right CDN base.
- **Optional:** add a `<...>Login</...>` button — it gets replaced with the user's name when they're logged in.

### 2. Commit and push

```bash
git add -A
git commit -m "Update sovereignty page"
git push
```

That's it — Cloudflare Pages picks up the push and builds a new deployment. Watch it under **Workers & Pages → `gooey-static-pages` → Deployments** in the [Cloudflare dashboard](https://dash.cloudflare.com/). The repo root is published as the site root, so the `sovereignty/` folder is served at `/sovereignty`.

<details>
<summary>Manual deploy (only if you can't push, or to test a one-off)</summary>

```bash
wrangler login                                      # one time
wrangler pages deploy . --project-name gooey-static-pages
```

Or drag the **repo root** folder into **Create deployment** on the project's dashboard page.

</details>

### 3. Verify

- CDN (raw): https://gooey-static-pages.pages.dev/sovereignty/
- Live (through gooey.ai): https://gooey.ai/sovereignty

## 📤 How to update the `/research` page

The papers, featured articles and profiles live in a Google Sheet, but the page
**does not read the sheet when a visitor loads it** — that cost is paid at build
time instead. The content is baked into `research/index.html`, so the page makes
zero external calls for its own data.

Updating content is therefore two steps: edit the sheet, then run the sync.

### 1. Edit the sheet

Three tabs — **Papers**, **Articles**, **Profiles**:

| Tab | Columns |
| --- | --- |
| `Papers` | `slug, type, title, venue, date, note, note_url, logo, blurb, authors, pdf_url, tags, hidden` |
| `Articles` | `column, title, meta, url, hidden` |
| `Profiles` | `name, role, photo, link_label, link_url, hidden` |

- **`slug`** is what joins a row to its paper page — it must match the folder name
  under `research/papers/`. Leave it blank and the page falls back to deriving a slug
  from the title, which is fine until someone rewords the title: the link then breaks
  silently. Fill it in for any paper that has a page.
- **`tags`** drives the filter chips — comma-separated, e.g. `Culture, India`. A new tag
  automatically becomes a new chip (sorted alphabetically after `All`).
- **`hidden`** — put `yes` to pull a row off the page without deleting it.
- **`logo` / `photo`** take a repo-relative path (`images/venues/icml-2026.png`) or a full
  `https://` URL. New image files go under `research/images/` and get committed.
- **`pdf_url` / `note_url` / `url`** must be `http(s)`. Anything else is stripped at render
  time — the sheet is domain-editable, so the page does not trust it.
- **Keep every column formatted as plain text** (select all → Format → Number → Plain
  text). Google auto-converts anything that looks like a date or number, and the export
  then returns **empty** for cells in that column that don't match the inferred type — a
  `date` column holding both `16 April` and `25–27 November` silently drops the second.
  The sync script warns when it sees a suspicious blank.

### 2. Run the sync

```bash
python3 scripts/sync_research.py
```

It fetches the three tabs and rewrites:

- `research/data/*.csv` — a readable snapshot, so `git diff` shows exactly what content
  changed
- the `<script type="application/json" id="research-data">` block in
  `research/index.html` — what the page actually renders

Use `--dry-run` to see what would change without writing. Then commit and push; Cloudflare
Pages deploys as usual. **Sheet edits are not live until this runs** — that is the
intended trade: no page-load dependency on Google.

The sheet id lives at the top of `scripts/sync_research.py`. The sheet still needs
**Anyone with the link = Viewer** for the script to read it, plus **gooey.ai as Editor**
so the team can edit it.

### Paper pages

Each paper we host a PDF for gets its own page, laid out to match the "Shaping AI
Paper" artboard: sticky contents sidebar, title, authors, abstract card, numbered
sections, figures, and references.

```bash
python3 scripts/build_papers.py
```

- The text lives in [`scripts/papers_content.py`](scripts/papers_content.py), one entry
  per paper. Edit there and re-run — never edit `research/papers/<slug>/index.html` by hand, it is
  overwritten.
- `listed_as` in each entry must match the paper's `title` in the Sheet **exactly**.
  That is the join between a row in the list and its page.
- The build writes the `PAPER_PAGES` manifest into `research/index.html`. A paper in
  that manifest gets a linked title and a PDF button; **a paper without a hosted PDF
  gets neither.** The Sheet's `pdf_url` column is no longer read by the page.
- Add a paper by dropping `research/pdf/<slug>.pdf`, extracting its figures to
  `research/papers/<slug>/fig-N.jpg`, adding an entry to `papers_content.py`, and
  re-running. The build fails loudly if a referenced figure is missing.

#### Why paper pages carry a second `<base>` tag

Paper pages sit two levels down, at `/research/papers/<slug>/`, but **every asset
path in them is relative to `research/`** — `pdf/x.pdf`, `papers/<slug>/fig-1.jpg`,
`assets/fonts/...`. That is because the gooey.ai proxy pins the base to the
top-level folder; `/sovereignty` is served with
`<base href="https://gooey-static-pages.pages.dev/sovereignty/">`.

Served directly — the raw CDN, or a local preview — nothing injects a base, so the
same relative paths would resolve from the page's own folder and 404. Each paper
page therefore carries its own `<base href="../../">` **after** the
`<!-- GOOEY-BASE-HREF -->` comment. Only the first `<base>` in a document counts,
so the proxy's wins in production and the fallback applies everywhere else. Both
resolve to `research/`.

Keep that ordering if you hand-edit the template.

`fig-N.jpg` files came out of the PDFs with `pdfimages -png`, de-duplicated (every
PDF carries masks and repeated page furniture), then downscaled to 1600px wide.

### Editing the page itself

Edit `research/index.html` for anything that isn't sheet content — the hero, "Our vision",
"Focus areas", the card "All" links (`COLUMN_LINKS`) and the chip icons (`TAG_ICONS`).
Don't hand-edit the JSON block; `sync_research.py` overwrites it.

Keep the same proxy requirements as sovereignty:

- **Required:** keep the `<!-- GOOEY-BASE-HREF -->` comment in `<head>`.
- **Required:** use **relative** asset paths (`assets/x.png`, `images/y.png`).
- **Optional:** keep the `Login` button for logged-in user name substitution.

### Verify

- CDN (raw): https://gooey-static-pages.pages.dev/research/
- Live (through gooey.ai): https://gooey.ai/research
