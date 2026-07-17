"""
10_merge_journal_metadata.py
Merges the publisher/subject_area metadata fetched by
09_fetch_journal_metadata.py into journal_subscription_summary.csv.

Left join on issn_l -- a journal missing from the metadata fetch (e.g. an ID
that no longer resolves) just gets blank publisher/subject_area rather than
being dropped from the table.
"""
import glob
import pandas as pd

SUBSCRIPTION_SUMMARY_FILE = "journal_subscription_summary.csv"
METADATA_DIR = "data/journal_metadata"


def load_parquets(pattern):
    files = glob.glob(pattern)
    if not files:
        raise RuntimeError(f"No files matched {pattern} -- did 09_fetch_journal_metadata.py finish?")
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def main():
    subscription = pd.read_csv(SUBSCRIPTION_SUMMARY_FILE, dtype={"issn_l": str})

    # Drop any metadata columns from a previous merge before joining again --
    # otherwise re-running this script (e.g. after re-fetching with new
    # fields) causes pandas to rename colliding columns to _x/_y suffixes
    # instead of overwriting them.
    metadata_cols = ["publisher", "subject_area", "subject_domain", "subject_field", "subject_subfield", "source_type"]
    subscription = subscription.drop(columns=[c for c in metadata_cols if c in subscription.columns])

    metadata = load_parquets(f"{METADATA_DIR}/batch_*.parquet")

    # a handful of ISSN-Ls can resolve to more than one Source record in rare
    # cases (merged/renamed journals) -- keep the first to avoid row duplication
    metadata = metadata.drop_duplicates(subset="issn_l", keep="first")

    merged = subscription.merge(metadata, on="issn_l", how="left")
    merged.to_csv(SUBSCRIPTION_SUMMARY_FILE, index=False)

    matched = merged["publisher"].notna().sum()
    print(f"Wrote {SUBSCRIPTION_SUMMARY_FILE} ({len(merged)} journals, "
          f"{matched} matched to publisher/subject_area metadata)")


if __name__ == "__main__":
    main()
