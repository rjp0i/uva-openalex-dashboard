"""
07_build_dashboard_data.py
Builds one compact JSON file (dashboard_data.json) containing everything the
static dashboard needs -- publication stats/trends/OA/journals/funders/topics
from data/uva_works, PLUS the citation-to-journal data from the CSVs produced
by 04_aggregate_counts.py (something the old live-query dashboard never had,
since it requires the expensive reference-fetching step).

No raw per-work data goes into the JSON except a small top-N cited works
table -- everything else is pre-aggregated, so this stays small (KBs, not MBs)
regardless of how many works/citations underlie it.
"""
import glob
import json
import datetime
import pandas as pd

UVA_WORKS_DIR = "data/uva_works"
SUBSCRIPTION_SUMMARY_FILE = "journal_subscription_summary.csv"  # pre-consolidation, per-ISSN
CONSOLIDATED_SUMMARY_FILE = "journal_subscription_summary_by_title.csv"  # post-consolidation, by title
OUT_FILE = "dashboard_data.json"

TOP_N_CHARTS = 15
TOP_N_CITED_WORKS = 20


def load_parquets(pattern):
    files = glob.glob(pattern)
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def top_n_counts(series, n):
    counts = series.value_counts().head(n)
    return [{"name": str(k), "count": int(v)} for k, v in counts.items()]


def main():
    works = load_parquets(f"{UVA_WORKS_DIR}/page_*.parquet")

    # ---- top-level stats ----
    total_works = len(works)
    oa_works = int(works["is_oa"].sum())
    total_citations = int(works["cited_by_count"].fillna(0).sum())
    stats = {
        "total_works": total_works,
        "oa_works": oa_works,
        "oa_pct": round(100 * oa_works / total_works, 1) if total_works else 0,
        "total_citations_received": total_citations,
        "avg_citations_per_work": round(total_citations / total_works, 1) if total_works else 0,
        "distinct_journals_published_in": int(works["issn_l"].nunique()),
    }

    # ---- publication trends by year ----
    trends = (
        works.dropna(subset=["publication_year"])
        .groupby("publication_year").size()
        .reset_index(name="count")
        .sort_values("publication_year")
    )
    trends = [{"year": int(r.publication_year), "count": int(r.count)} for r in trends.itertuples()]

    # ---- OA status breakdown ----
    oa_breakdown = top_n_counts(works["oa_status"].fillna("closed"), 10)

    # ---- top funders ----
    funder_series = works["funder_names"].explode().dropna()
    top_funders = top_n_counts(funder_series, TOP_N_CHARTS)

    # ---- top cited works ----
    top_cited = works.dropna(subset=["cited_by_count"]).sort_values(
        "cited_by_count", ascending=False
    ).head(TOP_N_CITED_WORKS)
    top_cited_works = [
        {
            "title": r.display_name or "Untitled",
            "year": int(r.publication_year) if pd.notna(r.publication_year) else None,
            "is_oa": bool(r.is_oa),
            "citations": int(r.cited_by_count),
            "url": r.id.replace("https://openalex.org/", "https://explore.openalex.org/works/"),
        }
        for r in top_cited.itertuples()
    ]

    # ---- pre-consolidation (per-ISSN) table -- used ONLY for Top Publishers,
    # which specifically wants ISSNs/publishers kept distinct even when the
    # same title spans a publisher change ----
    per_issn = pd.read_csv(SUBSCRIPTION_SUMMARY_FILE).fillna("")

    if "publisher" in per_issn.columns:
        pub_df = per_issn[per_issn["publisher"] != ""].copy()
    else:
        pub_df = pd.DataFrame()
    if len(pub_df):
        top_publishers = (
            pub_df.groupby("publisher")["uva_works_total"].sum()
            .sort_values(ascending=False).head(TOP_N_CHARTS)
        )
        top_publishers = [{"name": str(k), "count": int(v)} for k, v in top_publishers.items()]
    else:
        top_publishers = []

    # ---- consolidated-by-title table -- used for everything else, so a
    # journal that changed ISSN (publisher switch, relaunch, etc.) shows up
    # ONCE with combined totals, not scattered across multiple rows ----
    try:
        subscription = pd.read_csv(CONSOLIDATED_SUMMARY_FILE).fillna("")
    except FileNotFoundError:
        print(f"NOTE: {CONSOLIDATED_SUMMARY_FILE} not found -- falling back to the "
              f"per-ISSN file for everything (run 11_apply_title_overrides.py and "
              f"12_consolidate_by_title.py to enable title consolidation).")
        subscription = per_issn.rename(columns={"issn_l": "issn_ls", "publisher": "publishers"})

    subscription_summary = subscription.to_dict(orient="records")

    # ---- top journals UVA publishes in / cites, from the consolidated table ----
    # Excludes repository-type sources (SSRN, arXiv, institutional repositories,
    # etc.) -- OpenAlex doesn't track a true "peer reviewed" flag, but Source.type
    # ("journal" vs "repository"/"conference"/etc.) is the closest principled
    # signal available, and much more reliable than relying on which sources
    # happen to have an ISSN-L registered (an accident of cataloguing, not a
    # meaningful distinction -- e.g. SSRN has one, arXiv doesn't).
    def top_journals(sort_col):
        candidates = subscription[subscription[sort_col] > 0]
        if "source_type" in subscription.columns:
            candidates = candidates[
                (candidates["source_type"] == "journal") | (candidates["source_type"] == "")
            ]
        ranked = candidates.sort_values(sort_col, ascending=False).head(TOP_N_CHARTS)
        return [
            {"issn_ls": r.issn_ls, "name": r.source_name or r.issn_ls, "count": int(getattr(r, sort_col))}
            for r in ranked.itertuples()
        ]

    top_journals_published = top_journals("uva_works_total")
    top_journals_cited = top_journals("uva_citations_total")

    # ---- top subject areas (domain/field/subfield) ----
    # Ranked by uva_works_total ONLY (not combined with citations) -- this is
    # meant to show where UVA AUTHORS PUBLISH, so weighting by citation volume
    # (which dwarfs publishing volume) would just be showing citation volume
    # with publishing noise mixed in, not UVA's own output profile.
    def top_subject_level(level_col):
        if level_col not in subscription.columns:
            return []
        level_df = subscription[subscription[level_col] != ""].copy()
        if not len(level_df):
            return []
        grouped = (
            level_df.groupby(level_col)["uva_works_total"].sum()
            .sort_values(ascending=False).head(TOP_N_CHARTS)
        )
        return [{"name": str(k), "count": int(v)} for k, v in grouped.items()]

    top_subject_domains = top_subject_level("subject_domain")
    top_subject_fields = top_subject_level("subject_field")
    top_subject_subfields = top_subject_level("subject_subfield")

    bundle = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "stats": stats,
        "trends": trends,
        "oa_breakdown": oa_breakdown,
        "top_journals_published": top_journals_published,
        "top_journals_cited": top_journals_cited,
        "top_funders": top_funders,
        "top_publishers": top_publishers,
        "top_subject_domains": top_subject_domains,
        "top_subject_fields": top_subject_fields,
        "top_subject_subfields": top_subject_subfields,
        "top_cited_works": top_cited_works,
        "subscription_summary": subscription_summary,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(bundle, f)

    print(f"Wrote {OUT_FILE}")
    print(f"  {stats['total_works']} works, {len(subscription_summary)} journals in subscription table")


if __name__ == "__main__":
    main()
