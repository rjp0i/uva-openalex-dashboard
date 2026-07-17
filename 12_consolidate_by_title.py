"""
12_consolidate_by_title.py
Consolidates rows that share the same (corrected) title but different ISSN-Ls
-- common when a journal changes publisher and gets reassigned a new ISSN-L
in OpenAlex, even though it's really the same continuing publication.

Run AFTER 11_apply_title_overrides.py, since consolidation groups by title --
a mangled or missing title would either fail to group correctly or group
under a corrupted string.

Produces a SEPARATE file (journal_subscription_summary_by_title.csv) rather
than overwriting journal_subscription_summary.csv, because the Top
Publishers chart specifically wants ISSNs kept distinct (so a publisher
switch is visible as itself, not absorbed into a merged total) -- everything
else (the bottom table, Top Journals Published/Cited, subject charts) should
use the consolidated version instead.
"""
import pandas as pd

SUBSCRIPTION_SUMMARY_FILE = "journal_subscription_summary.csv"
OUT_FILE = "journal_subscription_summary_by_title.csv"

SUM_COLS = [
    "uva_works_total", "uva_works_oa", "uva_works_non_oa",
    "uva_citations_total", "uva_citations_oa", "uva_citations_non_oa",
]


def join_unique(series):
    seen = []
    for v in series:
        if pd.notna(v) and v != "" and v not in seen:
            seen.append(v)
    return "; ".join(seen)


def pick_representative(group, col):
    """For fields that should have ONE value per title (subject classification),
    pick the value associated with the row with the most combined activity --
    i.e. the most well-attested incarnation of the journal, not just the first
    row alphabetically."""
    non_blank = group[group[col].notna() & (group[col] != "")]
    if not len(non_blank):
        return ""
    activity = non_blank["uva_works_total"] + non_blank["uva_citations_total"]
    return non_blank.loc[activity.idxmax(), col]


def main():
    subscription = pd.read_csv(SUBSCRIPTION_SUMMARY_FILE, dtype={"issn_l": str})

    # journals with no title at all can't be grouped by title -- keep them
    # as their own singleton groups (keyed by issn_l instead) rather than
    # accidentally lumping every untitled journal into one giant group
    no_title = subscription["source_name"].isna() | (subscription["source_name"] == "")
    subscription = subscription.copy()
    subscription["_group_key"] = subscription["source_name"]
    subscription.loc[no_title, "_group_key"] = "MISSING_TITLE:" + subscription.loc[no_title, "issn_l"].fillna("UNKNOWN_ISSN")

    unusable = (subscription["_group_key"] == "MISSING_TITLE:UNKNOWN_ISSN").sum()
    if unusable:
        print(f"WARNING: {unusable} row(s) have neither a title nor an issn_l -- "
              f"these can't be meaningfully grouped and will show up merged together "
              f"under one placeholder row. Shouldn't happen with real pipeline data.")

    rows = []
    for group_key, group in subscription.groupby("_group_key"):
        row = {
            "source_name": group["source_name"].iloc[0] if not no_title[group.index].all() else "",
            "issn_ls": join_unique(group["issn_l"]),
            "publishers": join_unique(group["publisher"]) if "publisher" in group.columns else "",
            "source_type": pick_representative(group, "source_type") if "source_type" in group.columns else "",
            "subject_domain": pick_representative(group, "subject_domain") if "subject_domain" in group.columns else "",
            "subject_field": pick_representative(group, "subject_field") if "subject_field" in group.columns else "",
            "subject_subfield": pick_representative(group, "subject_subfield") if "subject_subfield" in group.columns else "",
            "n_issns_merged": group["issn_l"].nunique(),
        }
        for col in SUM_COLS:
            row[col] = int(group[col].sum())
        rows.append(row)

    out = pd.DataFrame(rows)

    ratio = out["uva_works_total"] / out["uva_citations_total"].astype(float)
    out["works_to_citations_ratio"] = ratio.replace(
        [float("inf"), float("-inf")], float("nan")
    ).apply(lambda x: x if pd.isna(x) or x == 0 else round(x, 2))

    out = out.sort_values(["uva_works_total", "uva_citations_total"], ascending=False)
    out.to_csv(OUT_FILE, index=False)

    merged_count = (out["n_issns_merged"] > 1).sum()
    print(f"Wrote {OUT_FILE} ({len(out)} title groups, {merged_count} of which "
          f"merged multiple ISSN-Ls)")


if __name__ == "__main__":
    main()
