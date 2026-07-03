#!/usr/bin/env python3
"""Scrape Lois McMaster Bujold's Goodreads Ask the Author Q&A into data/questions.json.

Every run is a full scrape of the list endpoint (78-ish pages), which refreshes
question text, answers, like counts, and comment counts for the whole corpus.
Comment threads are fetched only for questions whose comment count changed
(i.e. everything with comments on the first run, only new activity after).
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

BASE = "https://www.goodreads.com"
LIST_URL = BASE + "/author/16094.Lois_McMaster_Bujold/questions"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "questions.json"
META_FILE = ROOT / "data" / "meta.json"
CACHE_DIR = ROOT / ".cache"

ALLOWED_TAGS = {"a", "b", "i", "em", "strong", "br", "p", "blockquote", "ul", "ol", "li"}

session = requests.Session()
session.headers["User-Agent"] = UA


def fetch(url, delay, cache_path=None, read_cache=False, retries=3):
    """GET with polite spacing and simple retry.

    Responses are always written to .cache/ so the corpus can be re-parsed
    without re-scraping; the cache is only *read* when read_cache is set,
    so normal runs always fetch fresh data.
    """
    if read_cache and cache_path and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    last_err = None
    for attempt in range(retries):
        time.sleep(delay * (attempt + 1))
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                if cache_path:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(resp.text, encoding="utf-8")
                return resp.text
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
        print(f"  retry {attempt + 1} for {url}: {last_err}", file=sys.stderr)
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def unspoiler(node):
    """Replace Goodreads spoiler shrouds with their hidden content, in place.

    Spoilered text renders as a "This question/answer contains spoilers…"
    placeholder plus a hidden <span class="spoilerContainer">[ real text
    (hide spoiler) ]</span>. Returns True if any shroud was found.
    """
    if not node.select_one("span.spoilerContainer"):
        return False
    # Toggle links can sit inside the container (answers) or as siblings of it
    # (comments), so sweep the whole node.
    for a in node.select("a.spoilerAction, a.jsShowSpoiler, a.jsHideSpoiler"):
        a.decompose()
    for container in node.select("span.spoilerContainer"):
        # The real content is wrapped in literal [ ... ] brackets.
        strings = [s for s in container.find_all(string=True) if s.strip()]
        if strings:
            strings[0].replace_with(re.sub(r"^\s*\[", "", strings[0]))
        strings = [s for s in container.find_all(string=True) if s.strip()]
        if strings:
            strings[-1].replace_with(re.sub(r"\]\s*$", "", strings[-1]))
        # Swap the whole shroud (placeholder text + toggle links) for the content.
        shroud = container.find_parent(class_="spoiler") or container
        for child in list(container.contents):
            shroud.insert_before(child)
        shroud.decompose()
    return True


def sanitize(node):
    """Reduce a soup fragment to a small allowlist of tags; keep only href on <a>."""
    for tag in list(node.find_all(True)):
        if tag.name in ("script", "style"):
            tag.decompose()
        elif tag.name in ALLOWED_TAGS:
            href = tag.get("href", "")
            tag.attrs = {}
            if tag.name == "a" and href:
                if href.startswith("/"):
                    href = BASE + href
                tag.attrs["href"] = href
                tag.attrs["rel"] = "nofollow"
        else:
            tag.unwrap()
    html = "".join(str(c) for c in node.contents)
    html = re.sub(r"^(?:\s|<br\s*/?>)+|(?:\s|<br\s*/?>)+$", "", html)
    return re.sub(r"[ \t]*\n[ \t]*", "\n", html)


def parse_list_page(html):
    """Parse one page of the questions list into partial question records."""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for block in soup.select("div.communityQuestionAndAnswer"):
        q_text = block.select_one(".questionText")
        answer_span = block.select_one(".communityAnswerText")
        q_link = q_text.select_one("a[href*='/questions/']") if q_text else None
        if not q_link or not answer_span:
            continue
        href = q_link.get("href", "")
        m = re.search(r"/questions/(\d+)", href)
        if not m:
            continue
        qid = int(m.group(1))
        spoiler_q = unspoiler(q_text)
        spoiler_a = unspoiler(answer_span)

        block_html = str(block)
        am = re.search(r"community_answer[_/](\d+)", block_html)
        answer_id = int(am.group(1)) if am else None

        text = block.get_text(" ")
        lm = re.search(r"(\d+)\s+likes?\b", text)
        cm = re.search(r"(\d+)\s+comments?\b", text)

        records.append(
            {
                "id": qid,
                "answer_id": answer_id,
                "url": BASE + href.split("?")[0],
                "question": q_text.get_text(" ", strip=True),
                "answer": sanitize(answer_span),
                "spoiler": spoiler_q or spoiler_a,
                "likes": int(lm.group(1)) if lm else 0,
                "comment_count": int(cm.group(1)) if cm else 0,
            }
        )
    return records


def parse_comments_page(html):
    """Parse one page of a comment thread. Returns (comments, total)."""
    soup = BeautifulSoup(html, "html.parser")
    total = 0
    tm = re.search(r"Showing\s+\d+-\d+\s+of\s+([\d,]+)", soup.get_text(" "))
    if tm:
        total = int(tm.group(1).replace(",", ""))
    comments = []
    for div in soup.select("div.comment"):
        num_div = div.find("div", id=re.compile(r"^comment_number_(\d+)$"))
        number = int(re.search(r"(\d+)$", num_div["id"]).group(1)) if num_div else 0

        author, author_url = "", ""
        author_a = div.select_one(".commentAuthor a")
        if author_a:
            author = author_a.get("title") or author_a.get_text(strip=True)
            author_url = author_a.get("href", "")
            if author_url.startswith("/"):
                author_url = BASE + author_url
        else:
            # Deleted accounts render as bare text: "[deleted user]"
            span = div.select_one(".commentAuthor")
            if span:
                author = span.get_text(strip=True)

        posted_at = ""
        date_div = div.find("div", class_="right", attrs={"title": True})
        if date_div:
            raw = date_div["title"].strip()
            posted_at = raw
            for fmt in ("%b %d, %Y %I:%M%p", "%B %d, %Y %I:%M%p"):
                try:
                    posted_at = datetime.strptime(raw, fmt).strftime("%Y-%m-%dT%H:%M")
                    break
                except ValueError:
                    continue

        body = div.select_one(".reviewText")
        text = ""
        if body:
            for icon in body.select("a.commenterIcon, img"):
                icon.decompose()
            unspoiler(body)
            text = sanitize(body)

        comments.append(
            {
                "number": number,
                "author": author,
                "author_url": author_url,
                "posted_at": posted_at,
                "text": text,
            }
        )
    return comments, total


def fetch_comments(answer_id, delay, read_cache=False):
    """Fetch the full comment thread for an answer, oldest first."""
    comments, page = [], 1
    while True:
        url = f"{BASE}/community_answer/{answer_id}/comments?page={page}"
        cache = CACHE_DIR / "comments" / f"{answer_id}_p{page}.html"
        batch, total = parse_comments_page(
            fetch(url, delay, cache_path=cache, read_cache=read_cache)
        )
        comments.extend(batch)
        if not batch or len(comments) >= total:
            break
        page += 1
    comments.sort(key=lambda c: c["number"])
    for c in comments:
        c.pop("number", None)
    return comments


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-pages", type=int, default=None, help="limit list pages (testing)")
    ap.add_argument("--list-only", action="store_true", help="skip comment fetching")
    ap.add_argument("--delay", type=float, default=1.2, help="seconds between requests")
    ap.add_argument(
        "--use-cache",
        action="store_true",
        help="re-parse from .cache/ instead of fetching (development)",
    )
    args = ap.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = {}
    if DATA_FILE.exists():
        existing = {q["id"]: q for q in json.loads(DATA_FILE.read_text(encoding="utf-8"))}

    # Phase 1: walk the list endpoint.
    records, page, total_pages, num_results = [], 1, None, None
    while total_pages is None or page <= total_pages:
        if args.max_pages and page > args.max_pages:
            break
        url = f"{LIST_URL}?format=json&page={page}&sort=newest"
        cache = CACHE_DIR / "list" / f"page_{page}.json"
        payload = json.loads(
            fetch(url, args.delay, cache_path=cache, read_cache=args.use_cache)
        )
        if not payload.get("ok"):
            raise RuntimeError(f"list page {page}: ok=false")
        total_pages = payload["total_pages"]
        num_results = payload.get("num_results")
        batch = parse_list_page(payload["content_html"])
        records.extend(batch)
        print(f"list page {page}/{total_pages}: {len(batch)} questions", flush=True)
        page += 1

    # Dedupe by id (a new question arriving mid-scrape shifts pages).
    seen, questions = set(), []
    for rec in records:
        if rec["id"] in seen:
            continue
        seen.add(rec["id"])
        questions.append(rec)

    # Never commit a shrinking dataset (unless this is a partial test run).
    if existing and not args.max_pages and len(questions) < len(existing):
        print(
            f"ABORT: scrape found {len(questions)} questions but existing data has "
            f"{len(existing)} — refusing to overwrite good data with a partial scrape.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Phase 2: comments, only where the count changed.
    to_fetch = []
    for rec in questions:
        old = existing.get(rec["id"])
        if rec["comment_count"] == 0:
            continue
        if (
            old is None
            or old.get("comment_count") != rec["comment_count"]
            or len(old.get("comments", [])) != rec["comment_count"]
            # --use-cache runs re-parse every cached thread, so parser fixes
            # propagate to stored comments without any network traffic.
            or args.use_cache
        ):
            to_fetch.append(rec)
    print(f"fetching comments for {len(to_fetch)} of {len(questions)} questions", flush=True)

    for pos, rec in enumerate(questions):
        old = existing.get(rec["id"])
        rec["list_position"] = pos
        rec["first_seen"] = old["first_seen"] if old else today
        rec["comments"] = old.get("comments", []) if old else []

    if not args.list_only:
        for i, rec in enumerate(to_fetch, 1):
            if not rec["answer_id"]:
                continue
            rec["comments"] = fetch_comments(
                rec["answer_id"], args.delay, read_cache=args.use_cache
            )
            if i % 25 == 0 or i == len(to_fetch):
                print(f"  comments {i}/{len(to_fetch)}", flush=True)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    META_FILE.write_text(
        json.dumps(
            {
                "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "question_count": len(questions),
                "goodreads_num_results": num_results,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(questions)} questions to {DATA_FILE}", flush=True)


if __name__ == "__main__":
    main()
