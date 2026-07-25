# UVA Journal Citation Tracking — OpenAlex pipeline

Replaces the old `journal-search.R` workflow. Instead of downloading pre-fetched
RData/RDS blocks, this pulls data directly from the OpenAlex API and handles
any number of target journals in a single run.

## Setup

```bash
pip install requests pandas pyarrow
export OPENALEX_API_KEY="your-key-here"    # from openalex.org/settings/api
```

## Files, in run order

1. **`01_find_institution.py`** — one-time: prints candidate OpenAlex
   institution IDs for "University of Virginia." Confirm the right one and
   paste it into `02_fetch_uva_works.py`.
2. **`02_fetch_uva_works.py`** — fetches every UVA-authored work (paginated,
   resumable via checkpoint file).
3. **`03_fetch_referenced_works.py`** — collects every unique work cited by a
   UVA work, then batch-looks-up (100 IDs/call) each one's journal + year
   (resumable via checkpoint file, safe to stop/restart across job submissions).
4. **`04_aggregate_counts.py`** — the "full pipeline" step: reads the RAW
   fetched data (`data/uva_works/`, `data/referenced_works/`) and rebuilds
   everything from scratch -- `uva_works_by_journal_year.csv`,
   `citations_by_journal_year.csv`, `citations_by_journal_cited_year.csv`,
   `citations_by_journal_summary.csv`, AND `journal_subscription_summary.csv`.
   Run this after a genuine data refresh (new works fetched via 2/3).
5. **`05_journal_summary.py`** — standalone, lighter-weight alternative to
   part of what `04_` does: collapses `citations_by_journal_year.csv` into
   `citations_by_journal_cited_year.csv` and `citations_by_journal_summary.csv`
   without re-reading the raw parquet data. Useful if you only need to
   regenerate these two summary views and the underlying year-level data
   hasn't changed.
6. **`06_publishing_and_citing_summary.py`** — standalone, lighter-weight
   alternative to the LAST part of what `04_` does: recombines
   `uva_works_by_journal_year.csv` + `citations_by_journal_summary.csv` into
   `journal_subscription_summary.csv`, without re-aggregating anything
   upstream of that. Use this (rather than a full `04_` re-run) when only
   the subscription-summary logic itself changed (e.g. adding a new column).
7. **`07_build_dashboard_data.py`** — bundles everything into one static
   `dashboard_data.json` for the dashboard (see "Dashboard" below).
8. **`08_fiscal_year_report.py`** — research output listing for the VPR
   office, filtered to a fiscal-year date window.
9. **`09_fetch_journal_metadata.py`** — batch-fetches publisher, subject
   area, and source type (`journal` vs `repository`/`conference`/etc.) for
   every journal in `journal_subscription_summary.csv` (published-in AND
   cited-only alike), from OpenAlex's `/sources` endpoint. Cheap and
   resumable, same pattern as step 3. `source_type` is used downstream to
   exclude repositories (SSRN, arXiv, institutional repositories, etc.) from
   the Top Journals Published/Cited charts -- OpenAlex doesn't track a true
   "peer reviewed" flag, but this is the closest principled signal available,
   and more reliable than relying on which sources happen to have an ISSN-L
   registered (an accident of cataloguing -- e.g. SSRN has one, arXiv doesn't).
10. **`10_merge_journal_metadata.py`** — merges that metadata into
    `journal_subscription_summary.csv`.
11. **`11_apply_title_overrides.py`** — applies the master title lookup
    (`Title_lookup.csv`) to fix mojibake'd or missing titles. This file is
    meant to grow over time -- as authors publish in or cite new journals,
    a handful may occasionally surface with the same UTF-8/Mac OS Roman
    mojibake or missing-title issues OpenAlex's original output had. Add a
    row whenever you spot one, re-run this step (and `12_` after it), and
    the fix persists across every future refresh rather than needing to be
    reapplied by hand.
12. **`12_consolidate_by_title.py`** — consolidates rows that share the same
    (corrected) title but different ISSN-Ls, common when a journal changes
    publisher and OpenAlex assigns a new ISSN-L for what's really the same
    continuing publication. Produces `journal_subscription_summary_by_title.csv`
    as a SEPARATE file (doesn't touch the original), summing counts and
    combining ISSNs/publishers into semicolon-joined lists.
13. **`13_check_affiliation_granularity.py`** — diagnostic, not a pipeline
    step. Samples recent UVA works and reports what fraction of UVA-affiliated
    authorships have enough raw affiliation detail to identify a specific
    school/department, vs. just a bare "University of Virginia" mention. Used
    once, early on, to gauge whether department-level classification was even
    feasible before building steps 14-16 -- kept around in case you want to
    re-check coverage later (e.g. after a fresh full re-fetch).
14. **`14_classify_department.py`** — NOT run directly; a shared library of
    classification logic imported by step 15. Given one authorship's raw
    affiliation text, matches it against `department_school_lookup.csv` (built
    from `UVA_Schools_and_Departments.csv`) to resolve a specific department,
    falling back to a school-level keyword match (`SCHOOL_PATTERNS`, e.g.
    "School of Nursing") for schools with no traditional departments, and
    finally to a UVA Health System institution-ID fallback (see "Department &
    school classification" below for the full matching logic and edge cases).
15. **`15_apply_department_classification.py`** — applies step 14's classifier
    across every UVA-affiliated authorship in the fetched corpus. Handles
    propagation from an author's OTHER confidently-classified papers (for
    papers with too little affiliation detail on their own), flags authors
    with genuinely conflicting departments across their career (possible
    OpenAlex author-merging error, or a real career move), and supports a
    manual override file. Outputs `work_department_classification.csv`,
    `department_classification_review.csv`, and
    `authors_with_multiple_departments.csv`. See "Department & school
    classification" below.
16. **`16_build_department_pages_data.py`** — builds
    `department_dashboard_data.json`: per-department and per-school aggregates
    (works published, citations made, trends, OA breakdown, funders,
    publishers, top journals, top cited works, Books, Other) for the
    department/school breakdown page. See "Department/school dashboard page"
    below.
17. **`17_diagnose_darden_citations.py`** — diagnostic, not a pipeline step;
    a template for investigating why a specific department's footnote-credited
    (shared-appointment) works carry a disproportionate share of its citation
    volume. Adapt the department name/logic to investigate a different
    department if a similar anomaly shows up elsewhere.
18. *(reserved -- number skipped in the original build; no file named `18_`.)*
19. **`19_diagnose_book_classification.py`** — diagnostic, not a pipeline
    step; checks why a department/school's Books section might show fewer
    (or zero) results than expected -- walks through whether books have any
    UVA-matched authorship at all, whether there's raw affiliation text to
    classify, and what the classifier actually resolved, all within the SAME
    rolling window step 16 uses (an earlier version of this diagnostic that
    checked the full corpus instead gave a misleadingly optimistic picture).
20. **`20_spot_check_author.py`** — NOT part of the regular pipeline; generates
    ready-to-use search queries for cross-referencing a specific author
    against Scopus, Web of Science, Dimensions, and Lens.org. See "Data
    quality: single-source errors" below for why this exists.

**Run order for a routine full data refresh:**
`2 -> 3 -> 4 -> 9 -> 10 -> 11 -> 12 -> 7`, then `15 -> 16` if you also want the
department/school breakdown page refreshed (it depends on `02_`'s
authorship-level data and `09_`/`11_`/`12_`'s journal metadata, so it comes
after all of those).
(4 already produces `journal_subscription_summary.csv`, so 5/6 aren't needed
in this path -- they're for when you want to redo just part of what 4 does,
without re-touching the raw fetched data.)

Note that 9-12 must run AFTER 4 or 6 (they operate on
`journal_subscription_summary.csv`) and BEFORE 7 AND 16 (both read the
consolidated output).

If you want a slice for just a specific list of journals afterward, set
`OPTIONAL_TARGET_ISSN_CSV` at the top of `04_aggregate_counts.py` to a CSV
with a column named `issn_l` (see `target_journals_template.csv`) — this is
just a convenience filter on the full output, not a pipeline requirement.

Note: step 2 currently pulls `id, display_name, publication_year,
publication_date, type, primary_location, referenced_works, cited_by_count,
open_access, primary_topic, funders, authorships` (funders, not the
deprecated `grants` field OpenAlex used to use), and for each UVA-affiliated
authorship specifically (not co-authors from other institutions), captures
author ID, raw affiliation string(s), and which UVA institution(s) matched
(main UVA vs. Health System) into a `uva_authorships_json` column -- this is
what steps 14/15 classify against. If you're re-running from a previous
partial fetch made before some of these fields were added, clear the old
checkpoint and output first (`rm -rf data/uva_works
data/uva_works_checkpoint.json`) since the query itself changed.

## Cost / runtime expectations

With a free OpenAlex API key you get a $1/day budget, and list-style calls
(what this pipeline uses) cost $0.0001 each — about 10,000 calls/day for free.

- Step 2 (UVA's own works): with ~200 works/page, even 200,000+ UVA works over
  20+ years is roughly 1,000 calls — a few minutes, negligible cost.
- Step 3 (referenced works) is the big one: with millions of unique cited
  works at 100 per batch call, this could be tens of thousands of calls. At
  the free daily budget, a full 20-year historical backfill may take a few
  days of (resumable) runs to complete. Two options if that's too slow:
  - Just let it run across several HPC job submissions — it always picks up
    where it left off.
  - Email support@openalex.org and ask about a higher budget for academic/
    institutional use — they've indicated they'll often accommodate this.

Once the historical backfill is done, refreshing later is much cheaper: you'd
only need to re-run step 2 for new UVA works and step 3 for their (likely much
smaller) set of newly-referenced works.

## Data retrieval dates

Steps 2 and 3 each stamp a `retrieved_at` timestamp INSIDE their checkpoint
file (`data/uva_works_checkpoint.json`, `data/referenced_works_checkpoint.json`)
the moment they genuinely finish a fetch. Step 7 reads both and shows them on
the dashboard ("OpenAlex data retrieved: ...") -- distinct from
`generated_at`, which just reflects when the JSON itself was last assembled
(you can rebuild the JSON without re-fetching anything, e.g. after only
changing title overrides).

This is deliberately tied to the SAME checkpoint file you already clear
before a refresh, not a separate file -- so re-running an already-complete
step is a genuine no-op (checkpoint says done, nothing new fetched, old
timestamp correctly left alone), while clearing the checkpoint and running a
real refresh naturally produces a fresh, accurate timestamp with no extra
step to remember. If a from-scratch fetch ever needs a different design,
keep this coupling in mind -- a separate timestamp file is an easy trap
(three things to remember to clear instead of one, and forgetting the third
silently leaves a stale date after a legitimate refresh).

## Running on the cluster

Each script is a plain Python script with no MPI/multiprocessing — a single
CPU core is enough (the bottleneck is API latency, not compute). A simple
SLURM job like this works for the long-running steps:

```bash
#!/bin/bash
#SBATCH --job-name=oa-refs
#SBATCH --time=12:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=1

module load python
export OPENALEX_API_KEY="your-key-here"
python 03_fetch_referenced_works.py
```

## Dashboard

`dashboard_index.html` (rename to `index.html` in your GitHub Pages repo) is a
full rewrite of the old live-query dashboard. Instead of hitting the OpenAlex
API on page load (capped at up to 50K works, several seconds of waiting), it
just fetches the static `dashboard_data.json` produced by step 7 -- loads
instantly, and reflects your entire fetched corpus (~219K works, 3.1M+ unique
cited works) rather than a capped subset.

To deploy: copy `dashboard_index.html` (as `index.html`), `dashboard_data.json`,
`dashboard_departments.html`, `department_dashboard_data.json`, and both logo
files (`UVALIB_primary_color_web.png`, `UVALIB_centrd_color_web.png`) into
your `uva-openalex-dashboard` repo, alongside the existing `favicon.ico`, and
push. To refresh later with new data, re-run
`2 -> 3 -> 4 -> 9 -> 10 -> 11 -> 12 -> 7` (and `15 -> 16` for the department
page) and replace the two JSON files -- no code changes needed for a routine
data refresh.

### Branding

The dashboard uses UVA's official brand colors and font recommendations
(current as of when this was built -- check brand.virginia.edu if it's been
a while and something looks off):
- **Colors**: UVA Blue `#232D4B`, UVA Orange `#E57200`, plus the five
  secondary colors (Cyan, Teal, Green, Yellow, Magenta) -- all defined as CSS
  custom properties at the top of `dashboard_index.html`'s `<style>` block,
  so they're easy to find/adjust in one place if UVA's palette changes.
- **Fonts**: Libre Franklin (Google Fonts' free match for Franklin Gothic,
  UVA's primary brand typeface for headlines/body) throughout, and Libre
  Caslon Text (match for Adobe Caslon, the typeface in UVA's actual logo)
  used sparingly for just the page title and section headers. UVA's own
  licensed fonts require a paid Adobe/Typekit subscription; these are the
  specific free substitutes UVA's brand team publishes for non-vendor use.
- **Logo**: `UVALIB_primary_color_web.png` (horizontal lockup) sits in the
  masthead; `UVALIB_centrd_color_web.png` (stacked/centered lockup) is
  included too in case you want it somewhere narrower later, like a footer.
  Both are true transparent PNGs with navy logo text, which is why the
  masthead background is white (not UVA Blue) -- navy-on-navy would be
  illegible.

New in this version, beyond what the old dashboard showed:
- **Top Journals Cited** and the **Publishing vs. Citing** table -- both come
  from the citation-matrix pipeline (steps 3-4), which the old live-query
  version had no way to access (fetching reference lists for ~219K works
  isn't something you can do inline on page load).
- The Publishing-vs-Citing table is searchable and sortable, and covers every
  journal in your dataset (capped at showing 500 rows at a time for browser
  performance -- use the search box to narrow down). It also shows every
  journal's `source_type` (journal/repository/etc.) as a visible, searchable
  column -- repositories aren't hidden here, just excluded from the two
  "Top Journals" charts specifically (see step 9's note above).
- **Title consolidation**: journals that changed ISSN (publisher switch,
  relaunch, etc.) are shown ONCE with combined totals, everywhere except the
  Top Publishers chart -- that one specifically keeps ISSNs/publishers
  distinct, since the point of that chart is seeing publisher changes, not
  hiding them.
- **Data retrieval dates** shown near the top of the page (see "Data
  retrieval dates" section above).
- The old per-work "Top Research Fields" chart (from each UVA paper's own
  `primary_topic`) was dropped in favor of the journal-level Subject
  Domain/Field/Subfield charts -- the two were answering subtly different
  questions (a paper's own classification vs. its journal's aggregate
  profile) but sat next to each other with near-identical names, which was
  more confusing than useful.

## Department & school classification

Answers "what is each department/school actually publishing and citing
*right now*" -- a different question from the overview page's full-history
totals, and one OpenAlex's data makes genuinely harder to answer than it
sounds, since most affiliation strings (especially for non-medical fields)
just say "University of Virginia" with no department name at all.

### The reference files

- **`UVA_Schools_and_Departments.csv`** — the source-of-truth org chart
  (user-maintained): one column per school/division, department names listed
  underneath. The College of Arts & Sciences is deliberately split into three
  reporting groups (Humanities, Sciences, Social Sciences) matching how the
  Dean's office and the library both already think about it. A department
  listed under two schools with a "(joint with ...)" annotation (currently
  just Biomedical Engineering, under both Medicine and Engineering) is a
  genuinely shared appointment; some schools (Nursing, Law, Batten,
  Architecture, SCPS, Data Science) have no traditional departments at all
  and are matched at the school level only.
- **`department_school_lookup.csv`** — the reshaped long-format version of
  the above (`department_name, school_or_division, is_joint`), regenerated
  whenever the org chart changes.
- **`author_department_overrides.csv`** (optional; doesn't exist until you
  create one) — manual corrections keyed on OpenAlex author ID (stable;
  names collide, IDs don't), columns `author_id, department_or_school`.
  Takes precedence over everything else for that author. Build this from
  `department_classification_review.csv`'s worklist.

### Matching logic (`14_classify_department.py`)

For a given raw affiliation string, in order:
1. **Department name match** (longest names checked first, so e.g.
   "Electrical and Computer Engineering" matches before any shorter partial
   overlap could). A TRUE joint department (explicitly marked in the org
   chart) is credited to ALL its schools. A department name that happens to
   appear under multiple schools WITHOUT being marked joint (currently just
   "Accounting," under both Commerce/McIntire and Darden -- two genuinely
   separate departments that share a generic name, confirmed by hand) is
   flagged as ambiguous UNLESS the string itself disambiguates (e.g.
   mentions "Darden" directly). Critically, the DEPARTMENT itself is treated
   as confident even when the SCHOOL is ambiguous -- "Accounting" isn't in
   question, only which of the two Accounting departments it is.
2. **School-level keyword fallback** (`SCHOOL_PATTERNS`) for affiliation
   strings that name a school generically with no specific department, or
   for schools with no departments at all. Includes UVA's School of Data
   Science under all three names it's gone by (School of Data Science,
   Data Science Institute, and the DSI acronym).
3. **UVA Health System institution-ID fallback**: rather than trying to
   match an ever-growing list of clinical divisions and named research
   centers (Nephrology, Cardiovascular Research Center, etc. -- open-ended
   and high-maintenance), an authorship tagged with UVA Health System's
   OWN separate OpenAlex institution ID (distinct from main UVA) resolves
   to "Medicine (unspecified)" as a last resort, when no more specific
   department/school text match succeeds.
4. Otherwise, **unclassified**.

### Applying it across the corpus (`15_apply_department_classification.py`)

- **Author-history propagation**: if a paper's own affiliation string isn't
  specific enough to classify on its own, but that SAME author has a
  confidently-classified OTHER paper, the department propagates. Handles the
  common real-world pattern of a full affiliation on some papers, an
  abbreviated one on others.
- **Redundant school-match pruning**: if an author has both a specific
  department match (e.g. "Neurology") and, on a different paper, only a
  generic school-level match for that department's OWN parent school (e.g.
  "Medicine," from a paper that just said "School of Medicine"), the
  generic one is pruned before propagation/conflict-checking -- it's almost
  certainly the same appointment captured with less detail, not a second one.
- **`shared_appointment_unresolved`**: a paper that couldn't classify on its
  own, from an author known (from their OTHER papers) to have genuine
  standing in MULTIPLE departments (a real joint appointment). Carries every
  candidate department/school forward rather than discarding the
  information, so a department page can credit it as a footnote. Kept
  strictly separate from `needs_review` (true unknowns) throughout --
  never blended into one number.
- **`authors_with_multiple_departments.csv`**: authors whose papers
  confidently resolve to genuinely different departments over time -- not a
  classification problem (each such paper is still correct on its own), but
  a data-quality diagnostic. Flags whether the pattern looks like a clean
  temporal split (all of department A before some year, all of B after --
  a plausible real career move or cross-listed appointment) or an
  INTERLEAVED one (bouncing between two unrelated departments across
  overlapping years) -- the latter is the stronger tell for OpenAlex's known
  author-name-merging issue having combined two different real people under
  one author ID. Worth a manual look via `20_spot_check_author.py` before
  assuming either explanation.
- **Rolling 10-year window**: steps 16+ deliberately look only at the last
  10 years (as of whenever you regenerate the data), not full history -- the
  department page is meant to answer "what do our researchers need access
  to NOW," a different question from the overview page's complete-history
  totals. A department/school with genuinely low recent output (e.g. a
  small department, or one whose scholarship skews toward books published
  outside this window) will legitimately show sparse numbers; check
  `19_diagnose_book_classification.py`'s full-corpus vs. in-window
  comparison before assuming something's broken.

## Department/school dashboard page

`dashboard_departments.html` + `department_dashboard_data.json` (built by
`16_build_department_pages_data.py`). One page with a picker (grouped:
Schools & Divisions, then each school's Departments), rather than a
separate static page per department/school -- much less to maintain, and
the College of Arts & Sciences' three reporting divisions plus a
whole-college "Arts & Sciences" rollup option all live in the same picker.

- **Confident vs. footnote (shared-appointment) credit shown separately,
  everywhere** -- stat cards, and every chart/table is confident-only. A
  multi-department work is credited to ALL its departments (broadcast, never
  split/prorated -- prorating would manufacture a false signal specifically
  for the subscription decisions this dashboard exists to inform).
- **Top-N settings**: `TOP_N = 15` (journals published/cited, funders,
  publishers, book series) and `TOP_N_CITED_WORKS = 20` (Top Cited
  Works/Books tables), matching the overview page's own `TOP_N_CHARTS`/
  `TOP_N_CITED_WORKS` values for consistency between the two pages.
- **Books section** — full monographs only (`type == "book"`); book
  CHAPTERS are deliberately grouped with articles/everything else (a
  chapter is closer in kind to an article than to a standalone book).
  `excluded_book_sources.csv` (user-maintained, same growing-reference-file
  pattern as `Title_lookup.csv`) excludes self-deposit repositories (Zenodo,
  La Referencia, etc.) that let anyone upload anything and self-label its
  type -- a "book"-typed work hosted there isn't reliably a real,
  peer-reviewed book. Excluded entirely (not just hidden from a chart),
  since the concern is the underlying work's classification, not just its
  attribution.
- **"Other" stat** — a plain count (deliberately not charted) of
  everything that's neither "Articles" (article/review/book-chapter) nor a
  legitimate "Book" -- datasets, dissertations, editorials, letters,
  preprints, and anything excluded from Books. A sanity-check reminder of
  what ISN'T reflected in any chart on the page, both on the overview page
  (with a type-by-type breakdown on hover) and per department/school (a
  plain count, given the smaller per-unit numbers).
- **`publisher_aliases.csv`** (user-maintained) — consolidates publishers
  that show up under multiple, unlinked names in OpenAlex's OWN data (e.g.
  "RELX Group" and "Elsevier BV" are two separate, disconnected top-level
  publisher entities in OpenAlex, despite Elsevier being a real-world
  subsidiary of RELX) -- reflects inconsistent tagging in OpenAlex's source
  data, not anything in this pipeline. Applied on BOTH the overview and
  department pages' Top Publishers charts. Two-column format
  (`source_publisher, canonical_publisher`); the loader strips stray
  whitespace from both columns defensively, since a space typed after the
  comma is easy to introduce by hand and (being unquoted CSV) would
  otherwise silently become part of the value and break the match.

## Data quality: single-source errors

OpenAlex's own institution-tagging occasionally produces a false positive --
an author with no real UVA connection, incorrectly tagged as UVA-affiliated.
This is invisible to every diagnostic in this pipeline, since (from the
data alone) it looks identical to a legitimate but sparsely-documented UVA
author; there's no internal inconsistency to catch. The only reliable way to
catch it is corroboration against an INDEPENDENT source -- unlikely that two
separately-curated databases make the exact same mistake about the exact
same person. `20_spot_check_author.py` generates ready-to-use, verified
query syntax for Scopus and Web of Science (both support typed boolean
queries) plus instructions for Dimensions (filter-panel-based) and Lens.org
(structured field search) -- no API credentials needed, just faster/correct
queries to paste into access you already have. Meant for spot-checking
specific flagged authors (e.g. the sole author of one of only two or three
books credited to a department), not bulk/automated verification.

## A note on editing the CSV reference files

Several of these files (`Title_lookup.csv`, `excluded_book_sources.csv`,
`publisher_aliases.csv`, `author_department_overrides.csv`) are meant to be
hand-edited over time. **Avoid Excel for this**, especially on Mac --
it's an unreliable judge of plain-UTF-8 files without a byte-order mark, and
can silently mis-decode (or worse, re-save and permanently corrupt) an
accented character on open. A plain text editor (TextEdit in Plain Text
mode, VS Code, `nano`) won't try to guess or reinterpret the encoding the
way a spreadsheet application does. The loaders for these files are
defensive where practical (BOM-stripping, replacing genuinely invalid bytes
rather than mis-decoding the whole file, whitespace-stripping), but none of
that helps if the wrong CHARACTER was saved into the file in the first
place -- that needs fixing at the source.
