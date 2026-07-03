#!/usr/bin/env python3
"""First-pass automatic tagging for data/questions.json.

Keyword rules over question + answer text. Deliberately conservative: proper
nouns are matched case-sensitively, and ambiguous titles (Memory, Passage,
Legacy...) are left for hand-tagging rather than over-tagged.

Additive by default: question IDs already present in data/tags.json are left
untouched, so hand-curated tags survive re-runs. Use --retag-all to recompute
everything (only before hand-curation starts, or after backing up tags.json).

Always rewrites data/taglist.json (tag name + display group for the site).
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "data" / "questions.json"
TAGS = ROOT / "data" / "tags.json"
TAGLIST = ROOT / "data" / "taglist.json"

I = re.IGNORECASE

# (tag, group, pattern, flags). Order defines taglist order. Series tags come
# first in each group so the site shows them ahead of individual books.
RULES = [
    # --- The Vorkosigan Saga ---
    ("The Vorkosigan Saga", "vorkosigan",
     r"\b(Vorkosigan|Miles|Cordelia|Aral\b|Ekaterin|Barrayar\w*|Cetaganda\w*|"
     r"Betan?\b|Beta Colony|ImpSec|Sergyar|Vorbarr?a?\w*|Gregor|Illyan|Bothari|"
     r"Jackson'?s Whole|[Qq]uaddie|Vorpatril|Dendarii|Ivan\b(?! the Terrible)|Taura\b)", 0),
    ("Shards of Honor", "vorkosigan", r"Shards of Hono[u]?r", 0),
    ("The Warrior's Apprentice", "vorkosigan", r"Warrior'?s Apprentice", 0),
    ("The Vor Game", "vorkosigan", r"Vor Game", 0),
    ("Ethan of Athos", "vorkosigan", r"Ethan of Athos|\bAthos\b", 0),
    ("Borders of Infinity", "vorkosigan", r"Borders of Infinity", 0),
    ("Brothers in Arms", "vorkosigan", r"Brothers in Arms", 0),
    ("Mirror Dance", "vorkosigan", r"Mirror Dance", 0),
    ("Komarr", "vorkosigan", r"\bKomarr\b", 0),
    ("A Civil Campaign", "vorkosigan", r"Civil Campaign", 0),
    ("Diplomatic Immunity", "vorkosigan", r"Diplomatic Immunity", 0),
    ("Winterfair Gifts", "vorkosigan", r"Winterfair", 0),
    ("Captain Vorpatril's Alliance", "vorkosigan", r"Vorpatril'?s Alliance|\bCVA\b", 0),
    ("Cryoburn", "vorkosigan", r"Cryoburn", I),
    ("Gentleman Jole and the Red Queen", "vorkosigan",
     r"Gentleman Jole|Red Queen|GJ ?&? ?RQ|GJatRQ", 0),
    ("Falling Free", "vorkosigan", r"Falling Free", 0),
    # Characters (case-sensitive so 'miles' the distance doesn't match)
    ("Miles Vorkosigan", "vorkosigan", r"\bMiles\b", 0),
    ("Cordelia", "vorkosigan", r"\bCordelia\b", 0),
    ("Aral Vorkosigan", "vorkosigan", r"\bAral\b", 0),
    ("Ivan Vorpatril", "vorkosigan", r"\bIvan\b(?! the Terrible)", 0),
    # Exclude other Marks (Twain, Helprin...) by rejecting a capitalized surname,
    # while keeping the fully-named character himself.
    ("Mark Vorkosigan", "vorkosigan", r"\bMark\b(?!\s+[A-Z][a-z])|Mark Vorkosig", 0),
    ("Emperor Gregor", "vorkosigan", r"\bGregor\b", 0),
    ("Ekaterin", "vorkosigan", r"\bEkaterin", 0),
    ("Simon Illyan", "vorkosigan", r"\bIllyan\b", 0),
    ("Miles & Ekaterin's kids", "vorkosigan",
     r"Helen Natalia|Aral Alexander|(?:Miles|Ekaterin)[^.]{0,60}(?:children|kids|twins)", 0),
    # Worldbuilding flavor
    ("Uterine replicators", "vorkosigan", r"uterine replicator|replicator tech", I),
    ("Butter bugs", "vorkosigan", r"butter ?bug", I),
    ("Quaddies", "vorkosigan", r"[Qq]uaddie", 0),
    ("The haut", "vorkosigan", r"\bhaut\b", 0),
    ("Time of Isolation", "vorkosigan", r"Time of Isolation", I),
    ("ImpSec", "vorkosigan", r"ImpSec", 0),

    # --- World of the Five Gods ---
    ("World of the Five Gods", "fivegods",
     r"\b(Five Gods|Chalion|Penric|Desdemona|Cazaril|Ista\b|Quintarian|Quadrene|"
     r"Roknari|Ibra\b|the Bastard|Bastard'?s\b|Wealde?an?\b|Ingrey|Cedonia|"
     r"Martensbridge|Lodi\b|Orbas\b|Vilnoc)", 0),
    ("The Curse of Chalion", "fivegods", r"Curse of Chalion|\bCazaril\b", 0),
    ("Paladin of Souls", "fivegods", r"Paladin of Souls|\bIsta\b", 0),
    ("The Hallowed Hunt", "fivegods", r"Hallowed Hunt|\bIngrey\b", 0),
    ("Penric & Desdemona", "fivegods", r"\bPenric\b|\bDesdemona\b", 0),
    ("Penric's Demon", "fivegods", r"Penric'?s Demon", 0),
    ("Penric and the Shaman", "fivegods", r"Penric and the Shaman", 0),
    ("Penric's Fox", "fivegods", r"Penric'?s Fox", 0),
    ("Penric's Mission", "fivegods", r"Penric'?s Mission", 0),
    ("Mira's Last Dance", "fivegods", r"Mira'?s Last Dance", 0),
    ("The Prisoner of Limnos", "fivegods", r"Prisoner of Limnos|\bLimnos\b", 0),
    ("The Orphans of Raspay", "fivegods", r"Orphans of Raspay|\bRaspay\b", 0),
    ("The Physicians of Vilnoc", "fivegods", r"Physicians of Vilnoc", 0),
    ("Masquerade in Lodi", "fivegods", r"Masquerade in Lodi", 0),
    ("The Assassins of Thasalon", "fivegods", r"Assassins of Thasalon|\bThasalon\b", 0),
    ("Knot of Shadows", "fivegods", r"Knot of Shadows", 0),
    ("Demon Daughter", "fivegods", r"Demon Daughter", 0),
    ("Penric and the Bandit", "fivegods", r"Penric and the Bandit", 0),
    ("The Demonic Ox", "fivegods", r"Demonic Ox", 0),
    ("Darksight", "fivegods", r"Darksight", 0),
    # Worldbuilding flavor
    ("Demons & sorcerers", "fivegods", r"\bdemon|sorcer", I),
    ("Saints & the gods", "fivegods", r"\bsaint|\bthe gods\b|theolog|free will", I),
    ("Shamans & Great Beasts", "fivegods", r"\bshaman|great beast", I),

    # --- The Sharing Knife ---
    ("The Sharing Knife", "sharingknife",
     r"Sharing Knife|Lakewalker|\bFawn\b|\bDag\b|\bmalice\b|farmer girl", 0),
    ("Beguilement", "sharingknife", r"Beguilement", 0),
    ("Knife Children", "sharingknife", r"Knife Children", 0),

    # --- Other works ---
    ("The Spirit Ring", "other", r"Spirit Ring", 0),

    # --- Topics ---
    ("Writing process", "topic",
     r"\b(draft|outline|revis\w+|plott?ing|inspiration|writer'?s block|"
     r"writing process|creative process|point of view|POV\b|world-?building|"
     r"how do you write)\b", I),
    ("Publishing", "topic",
     r"\b(publish\w*|e-?book|audio ?book|narrator|cover art|translat\w+|Baen|"
     r"Subterranean|Kindle|Nook|Kobo|print edition|paperback|hardcover)\b", I),
    ("Adaptations", "topic",
     r"\b(movie|film|television|TV series|adaptation|screenplay|Netflix|"
     r"audio drama|graphic novel)\b", I),
    ("Future books", "topic",
     r"\b(sequel|next book|new book|another book|will you (?:ever )?write|"
     r"more stories|forthcoming|work[- ]in[- ]progress|what(?:'s| is) next)\b", I),
    ("Reading & influences", "topic",
     r"\b(recommend\w*|influenc\w*|favorite (?:author|book)|favourite|"
     r"inspired by|Sayers|Heyer|Tolkien|Cherryh|Pratchett|Kingfisher|Vernon)\b", I),
    ("Audiobooks & narrators", "topic", r"audio ?book|narrator|Grover Gardner|audible", I),
    ("Pronunciation", "topic", r"pronounc|pronunciation", I),
    ("Fanfiction", "topic", r"fan ?fic", I),
    # "Uncle Hugo's" is her local SF bookstore, not the award
    ("Hugo & awards", "topic", r"(?<!Uncle )\bHugo\b|\bNebula\b|\bawards?\b", I),
    ("Translations", "topic", r"translat", I),
    ("Conventions & signings", "topic", r"convention|Worldcon|book signing|signed cop", I),
    ("Maps", "topic", r"(?<!over the )\bmaps?\b", I),  # skip "all over the map"
    ("Names & naming", "topic",
     r"name[sd]? (?:come from|of|for)|how.{0,20}names?|naming", I),
    # \b(?!-) skips Horseriver, "horse-sual", "horse-first"
    ("Horses", "topic", r"\bhorse(?:s|back|woman|manship)?\b(?!-)", I),
    ("Cats", "topic", r"\bcats?\b", I),
]


# Hand-curated corrections, applied last so they survive --retag-all.
# Format: question id -> tags to remove (rule matched, but the question isn't
# actually about that).
REMOVE_TAGS = {
    31447746: ["Horses"],  # links a "horse-first" essay; question is about marketing
    1611070: ["Horses"],   # cart-before-horse idiom
}

# A book tag implies its series tag.
SERIES_TAG = {
    "vorkosigan": "The Vorkosigan Saga",
    "fivegods": "World of the Five Gods",
    "sharingknife": "The Sharing Knife",
}
GROUP_OF = {tag: group for tag, group, _, _ in RULES}
RULE_ORDER = {tag: i for i, (tag, _, _, _) in enumerate(RULES)}


def strip_url_hosts(text):
    """Drop scheme+host from URLs but keep path words as weak topic signal.

    'http://ddd.uab.cat/record/1801' must not match Cats, but the slug in
    'thehugoawards.org/2018-hugo-awards' is real signal worth keeping.
    """
    def repl(m):
        path = re.sub(r"^(?:\w+://)?[^/\s]+", "", m.group(0))
        return " " + re.sub(r"[/\-_.?=&#%~+]+", " ", path) + " "

    return re.sub(r"\w+://\S+|\bwww\.\S+", repl, text)


def tag_question(q):
    text = f"{q['question']}\n{re.sub(r'<[^>]+>', ' ', q['answer'])}"
    text = strip_url_hosts(text)
    tags = []
    for tag, _group, pattern, flags in RULES:
        if re.search(pattern, text, flags):
            tags.append(tag)
    for tag in list(tags):
        series = SERIES_TAG.get(GROUP_OF[tag])
        if series and series not in tags:
            tags.append(series)
    return sorted(tags, key=RULE_ORDER.get)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--retag-all", action="store_true",
                    help="recompute tags for ALL questions (clobbers hand edits)")
    args = ap.parse_args()

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    existing = json.loads(TAGS.read_text(encoding="utf-8")) if TAGS.exists() else {}

    tags = {} if args.retag_all else dict(existing)
    new = 0
    for q in questions:
        key = str(q["id"])
        if key in tags:
            continue
        tags[key] = tag_question(q)
        new += 1

    for qid, bad in REMOVE_TAGS.items():
        key = str(qid)
        if key in tags:
            tags[key] = [t for t in tags[key] if t not in bad]

    TAGS.write_text(
        json.dumps(tags, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )
    TAGLIST.write_text(
        json.dumps([{"name": t, "group": g} for t, g, _, _ in RULES], indent=1),
        encoding="utf-8",
    )
    tagged = sum(1 for v in tags.values() if v)
    print(f"tagged {new} new questions ({tagged}/{len(tags)} have at least one tag)")


if __name__ == "__main__":
    main()
