"""
09_fetch_journal_metadata.py
Batch-fetches journal-LEVEL metadata (publisher, subject classification) from
OpenAlex's /sources endpoint for every distinct ISSN-L in
journal_subscription_summary.csv -- covering every journal UVA either
publishes in OR cites, not just the ones UVA happens to publish in
(topic_field on UVA's own works never covers cite-only journals).

Cheap and resumable, same pattern as 03_fetch_referenced_works.py: 100
ISSN-Ls per batch call, checkpointed so it survives interruption.
"""
import os
import pandas as pd
from common import make_session, api_get, load_checkpoint, save_checkpoint

SUBSCRIPTION_SUMMARY_FILE = "journal_subscription_summary.csv"
OUT_DIR = "data/journal_metadata"
CHECKPOINT_FILE = "data/journal_metadata_checkpoint.json"
BATCH_SIZE = 100

# x_concepts turned out to be empty/deprecated in practice -- Sources now
# carry subject classification via "topics", each entry with its own
# domain/field/subfield and a "count" of how many of that source's works
# carry that topic (unlike works' primary_topic, which uses a "score" --
# sources use "count" instead, so we rank by that).
SELECT_FIELDS = "id,issn_l,display_name,host_organization_name,topics,type"


def flatten(source):
    topics = source.get("topics") or []
    top = max(topics, key=lambda t: t.get("count") or 0) if topics else None

    def level_name(level_key):
        if not top:
            return None
        level = top.get(level_key) or {}
        return level.get("display_name")

    return {
        "issn_l": source.get("issn_l"),
        "publisher": source.get("host_organization_name"),
        "source_type": source.get("type"),
        "subject_domain": level_name("domain"),
        "subject_field": level_name("field"),
        "subject_subfield": level_name("subfield"),
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    session = make_session()

    issns = sorted(pd.read_csv(SUBSCRIPTION_SUMMARY_FILE, dtype=str)["issn_l"].dropna().unique())
    print(f"{len(issns)} distinct journals to look up")

    batches = [issns[i:i + BATCH_SIZE] for i in range(0, len(issns), BATCH_SIZE)]
    state = load_checkpoint(CHECKPOINT_FILE, {"next_batch": 0})
    print(f"Resuming from batch {state['next_batch']} / {len(batches)}")

    for i in range(state["next_batch"], len(batches)):
        batch = batches[i]
        params = {
            "filter": f"issn:{'|'.join(batch)}",
            "select": SELECT_FIELDS,
            "per_page": BATCH_SIZE,
        }
        data = api_get(session, "sources", params=params)
        rows = [flatten(s) for s in data["results"]]
        pd.DataFrame(rows).to_parquet(f"{OUT_DIR}/batch_{i:06d}.parquet", index=False)

        if i % 50 == 0:
            print(f"  batch {i}/{len(batches)}")

        state["next_batch"] = i + 1
        save_checkpoint(CHECKPOINT_FILE, state)

    print("Done fetching journal metadata. See", OUT_DIR)


if __name__ == "__main__":
    main()
