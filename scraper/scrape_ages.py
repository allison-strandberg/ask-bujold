#!/usr/bin/env python3
"""One-time scrape of individual question pages for the answer's relative age.

Question pages show the answer's age as relative text ("52 days ago",
"8 years ago") — the only place Goodreads exposes when an answer was posted.
Output is data/answer_ages.json: {question_id: {"age": "...", "as_of": date}},
a snapshot consumed by infer_dates.py. Resumable: already-fetched IDs are
skipped, and pages are cached in .cache/questions/.

Going forward this never needs to run again — new questions get near-exact
dates from their first_seen scrape date.
"""

import json
import sys
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from scrape import CACHE_DIR, DATA_FILE, ROOT, fetch

AGES_FILE = ROOT / "data" / "answer_ages.json"


def main():
    delay = 1.2
    questions = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    ages = json.loads(AGES_FILE.read_text(encoding="utf-8")) if AGES_FILE.exists() else {}
    todo = [q for q in questions if str(q["id"]) not in ages]
    print(f"fetching {len(todo)} question pages ({len(ages)} already done)", flush=True)

    for i, q in enumerate(todo, 1):
        cache = CACHE_DIR / "questions" / f"{q['id']}.html"
        try:
            html = fetch(q["url"], delay, cache_path=cache, read_cache=True)
        except RuntimeError as e:
            print(f"  skipping {q['id']}: {e}", file=sys.stderr)
            continue
        el = BeautifulSoup(html, "html.parser").select_one(".communityAnswerTimestamp")
        ages[str(q["id"])] = {
            "age": el.get_text(strip=True) if el else None,
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        if i % 25 == 0 or i == len(todo):
            AGES_FILE.write_text(
                json.dumps(ages, indent=0, sort_keys=True), encoding="utf-8"
            )
            print(f"  {i}/{len(todo)}", flush=True)

    missing = [k for k, v in ages.items() if not v["age"]]
    print(f"done: {len(ages)} ages, {len(missing)} pages without a timestamp {missing[:10]}")


if __name__ == "__main__":
    main()
