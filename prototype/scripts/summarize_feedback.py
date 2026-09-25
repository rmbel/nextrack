from __future__ import annotations

import json
from pathlib import Path
from statistics import mean


PROTOTYPE_DIR = Path(__file__).resolve().parent.parent
INPUT_PATH = PROTOTYPE_DIR / "data" / "user_feedback.jsonl"
OUTPUT_PATH = PROTOTYPE_DIR / "user-feedback-summary.md"
AGENT_TRIAL_PREFIX = "[AGENT TRIAL "


def feedback_source(record: dict) -> str:
    # The existing platform stores only request_id, rating and comment. Trial posts
    # therefore carry explicit provenance in their comment; unlabelled entries
    # must not silently become human-study evidence.
    if record.get("comment", "").startswith(AGENT_TRIAL_PREFIX):
        return "agent"
    declared = record.get("evaluator_type")
    return declared if declared in {"human", "agent"} else "unclassified"


def render_summary(records: list[dict]) -> str:
    lines = ["# NextTrack feedback summary", "", f"- Total responses: `{len(records)}`", "",
             "Agent assessments, confirmed human feedback and unclassified entries are reported separately.",
             "Agent stars describe observed task/metadata fit, not listening enjoyment or human satisfaction.", ""]
    for source, title in (("agent", "Agent trial ratings"), ("human", "Confirmed human ratings"),
                          ("unclassified", "Unclassified ratings")):
        group = [record for record in records if feedback_source(record) == source]
        ratings = [record["rating"] for record in group if "rating" in record]
        legacy = [record["relevance_rating"] for record in group if "relevance_rating" in record]
        lines.extend([f"## {title}", "", f"- Responses: `{len(group)}`",
                      f"- Mean playlist rating: `{mean(ratings):.2f}/5` ({len(ratings)} responses)"
                      if ratings else "- Playlist rating: not measured"])
        if legacy:
            lines.append(f"- Historical usefulness: `{mean(legacy):.2f}/5` ({len(legacy)} responses)")
        lines.extend(["", "Comments:", ""])
        comments = [record.get("comment", "") for record in group if record.get("comment")]
        lines.extend(f"- {comment}" for comment in comments)
        if not comments:
            lines.append("- No written comments.")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    if not INPUT_PATH.exists():
        print("No user feedback has been collected yet.")
        return

    records = [
        json.loads(line)
        for line in INPUT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    OUTPUT_PATH.write_text(render_summary(records), encoding="utf-8")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
