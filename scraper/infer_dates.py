#!/usr/bin/env python3
"""Estimate the date each answer was posted → data/dates.json.

Goodreads never exposes exact answer dates, so this combines three signals,
in decreasing order of trust:

1. first_seen after the initial 2026-07-02 scrape — the weekly job noticed the
   question appear, so the answer date is known to within a week.
2. First comment timestamp — comments are exact-dated and usually arrive within
   days of the answer (the asker is notified). A first comment far outside the
   page-age window is treated as late-arriving chatter and ignored.
3. Page age — the "52 days ago" / "8 years ago" text captured one-time by
   scrape_ages.py. Coarse (precision = one unit), but covers everything.

Questions with no signal are interpolated between anchored neighbors (the list
is ordered newest-first). A final pass clamps estimates to be monotonically
non-increasing with list position, which the ordering guarantees.

dates.json is derived data — regenerated on every run, never hand-edited.
"""

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "data" / "questions.json"
AGES = ROOT / "data" / "answer_ages.json"
OUT = ROOT / "data" / "dates.json"

INITIAL_SCRAPE = date(2026, 7, 2)  # first_seen values on this date carry no signal

UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def parse_age(entry):
    """'8 years ago' as of a date -> (estimated date, precision in days)."""
    if not entry or not entry.get("age"):
        return None
    m = re.match(
        r"(?:about\s+)?(\d+|an?|one)\s+(minute|hour|day|week|month|year)s?\s+ago",
        entry["age"].strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    n = 1 if m.group(1).lower() in ("a", "an", "one") else int(m.group(1))
    unit = m.group(2).lower()
    as_of = date.fromisoformat(entry["as_of"])
    # minute/hour ago is still "today" — don't let the day-precision floor below
    # push the estimate back by n whole days.
    est = as_of - timedelta(days=n * UNIT_DAYS[unit])
    precision = max(UNIT_DAYS[unit], 1)
    return est, precision


def first_comment_date(q):
    dates = []
    for c in q["comments"]:
        try:
            dates.append(datetime.strptime(c["posted_at"], "%Y-%m-%dT%H:%M").date())
        except ValueError:
            pass
    return min(dates) if dates else None


def main():
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    questions.sort(key=lambda q: q["list_position"])
    ages = json.loads(AGES.read_text(encoding="utf-8")) if AGES.exists() else {}

    est = {}  # id -> (date, precision_days, source)
    for q in questions:
        qid = q["id"]
        page = parse_age(ages.get(str(qid)))
        comment = first_comment_date(q)
        seen = date.fromisoformat(q["first_seen"])

        if seen > INITIAL_SCRAPE:
            est[qid] = (seen, 7, "first_seen")
        elif comment and page:
            p_est, p_prec = page
            if comment <= p_est + timedelta(days=p_prec):
                # Consistent with (or older than) the page window; the answer
                # can't postdate its first comment, so the comment date wins.
                prec = 3 if comment >= p_est - timedelta(days=p_prec) else 30
                est[qid] = (comment, prec, "comment")
            else:
                est[qid] = (p_est, p_prec, "page")  # late-arriving comment
        elif comment:
            est[qid] = (comment, 3, "comment")
        elif page:
            est[qid] = (page[0], page[1], "page")

    # Interpolate the rest between nearest anchored neighbors.
    anchored = [(q["list_position"], q["id"]) for q in questions if q["id"] in est]
    by_pos = {q["list_position"]: q["id"] for q in questions}
    for q in questions:
        if q["id"] in est:
            continue
        pos = q["list_position"]
        newer = max((p for p, _ in anchored if p < pos), default=None)
        older = min((p for p, _ in anchored if p > pos), default=None)
        if newer is not None and older is not None:
            d0, _, _ = est[by_pos[newer]]
            d1, _, _ = est[by_pos[older]]
            frac = (pos - newer) / (older - newer)
            mid = d1 + timedelta(days=(d0 - d1).days * (1 - frac))
            prec = max(30, abs((d0 - d1).days) // 2)
        elif newer is not None or older is not None:
            ref = by_pos[newer if newer is not None else older]
            mid, prec = est[ref][0], max(30, est[ref][1])
        else:
            continue
        est[q["id"]] = (mid, prec, "interpolated")

    # Enforce newest-first monotonicity.
    running_min = None
    clamped = 0
    for q in questions:
        if q["id"] not in est:
            continue
        d, prec, src = est[q["id"]]
        if running_min and d > running_min:
            est[q["id"]] = (running_min, prec, src)
            clamped += 1
        else:
            running_min = d

    OUT.write_text(
        json.dumps(
            {
                str(qid): {"est": d.isoformat(), "precision_days": prec, "source": src}
                for qid, (d, prec, src) in est.items()
            },
            indent=0,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from collections import Counter
    srcs = Counter(s for _, _, s in est.values())
    print(f"dated {len(est)}/{len(questions)} questions "
          f"(sources: {dict(srcs)}; {clamped} clamped for monotonicity)")


if __name__ == "__main__":
    main()
