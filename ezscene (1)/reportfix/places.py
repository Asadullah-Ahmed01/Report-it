"""Campus map. Each place belongs to exactly one department: that is how routing works."""
from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Dict, List, Optional

PLACES: List[Dict] = [
    {"id": "cse-lab-1", "name": "CSE Lab 1", "dept": "CSE", "aliases": ["cse lab 1", "cs lab 1", "computer science lab 1"]},
    {"id": "cse-lab-2", "name": "CSE Lab 2", "dept": "CSE", "aliases": ["cse lab 2", "cs lab 2", "computer science lab 2"]},
    {"id": "cse-dept", "name": "CSE Department", "dept": "CSE", "aliases": ["cse department", "cse dept", "cse block", "cs department", "cse office"]},
    {"id": "ee-lab-1", "name": "EE Lab 1", "dept": "EE", "aliases": ["ee lab 1", "electrical lab 1"]},
    {"id": "ee-lab-2", "name": "EE Lab 2", "dept": "EE", "aliases": ["ee lab 2", "electrical lab 2"]},
    {"id": "ee-dept", "name": "EE Department", "dept": "EE", "aliases": ["ee department", "ee dept", "ee block", "electrical department"]},
    {"id": "seminar-hall", "name": "Seminar Hall", "dept": "EE", "aliases": ["seminar hall", "seminar room"]},
    # Classrooms exist only for CSE, EE and IS. A room number ("CSE classroom 5", "EE room 12") is kept.
    {"id": "cse-classroom", "name": "CSE Classroom", "dept": "CSE", "kind": "classroom",
     "aliases": ["cse classroom", "cse classrooms", "cse class", "cs classroom", "cse room", "cse cr", "cse lecture hall",
                 "computer science classroom"]},
    {"id": "ee-classroom", "name": "EE Classroom", "dept": "EE", "kind": "classroom",
     "aliases": ["ee classroom", "ee classrooms", "ee class", "ee room", "ee cr", "ee lecture hall", "electrical classroom"]},
    {"id": "is-classroom", "name": "IS Classroom", "dept": "IS", "kind": "classroom",
     "aliases": ["is classroom", "is classrooms", "is cr", "is lecture hall", "infosec classroom",
                 "information security classroom"]},
    {"id": "is-lab", "name": "IS Lab", "dept": "IS", "aliases": ["is lab", "infosec lab", "information security lab", "security lab"]},
    {"id": "server-room", "name": "Server Room", "dept": "IS", "aliases": ["server room", "network room", "data center"]},
    {"id": "is-dept", "name": "IS Department", "dept": "IS", "aliases": ["is department", "is dept", "is block", "infosec department"]},
    {"id": "lab-1", "name": "Lab 1", "dept": "Labs", "aliases": ["lab 1", "lab one"]},
    {"id": "lab-2", "name": "Lab 2", "dept": "Labs", "aliases": ["lab 2", "lab two"]},
    {"id": "lab-3", "name": "Lab 3", "dept": "Labs", "aliases": ["lab 3", "lab three"]},
    {"id": "physics-lab", "name": "Physics Lab", "dept": "Labs", "aliases": ["physics lab"]},
    {"id": "library", "name": "Main Library", "dept": "Library", "aliases": ["main library", "library", "libary", "librery", "liberary"]},
    {"id": "reading-hall", "name": "Reading Hall", "dept": "Library", "aliases": ["reading hall", "reading room"]},
    {"id": "cafeteria", "name": "Cafeteria", "dept": "Cafeteria", "aliases": ["cafeteria", "cafe", "canteen", "cafetaria", "cafteria", "caf"]},
    {"id": "tuck-shop", "name": "Tuck Shop", "dept": "Cafeteria", "aliases": ["tuck shop", "tuckshop"]},
]

PLACE_BY_ID: Dict[str, Dict] = {p["id"]: p for p in PLACES}


def _alias_regex(alias: str, numbered: bool = False) -> re.Pattern:
    body = r"\s*".join(re.escape(part) for part in alias.split())
    tail = r"(?![a-z0-9])"
    if not alias[-1].isdigit() and not numbered:
        tail += r"(?!\s*\d)"  # "is lab" must not swallow "is lab 3" / "lab 3"
    return re.compile(r"(?<![a-z0-9])" + body + tail, re.IGNORECASE)


_ALIAS_INDEX = sorted(
    [(alias, _alias_regex(alias, p.get("kind") == "classroom"), p["id"])
     for p in PLACES for alias in [p["name"].lower()] + p["aliases"]],
    key=lambda item: len(item[0]),
    reverse=True,
)

_KNOWN_LAB_MENTIONS = {
    re.sub(r"\s+", " ", a) for p in PLACES for a in [p["name"].lower()] + p["aliases"] if re.search(r"\d", a)
}
_NUMBERED_MENTION = re.compile(
    r"(?<![a-z0-9])((?:cse|cs|ee|is|physics)?\s*(?:lab|room|hall|block|floor)\s*-?\s*\d+)(?![a-z0-9])",
    re.IGNORECASE,
)


def match_place(text: str) -> Optional[str]:
    """Deterministic place matcher used by the fallback classifier."""
    low = text.lower()
    for _alias, rx, pid in _ALIAS_INDEX:
        if rx.search(low):
            return pid
    for token in re.findall(r"[a-z]{5,}", low):  # typo tolerance: "libray", "cafetria"
        close = get_close_matches(token, ["library", "cafeteria", "canteen"], n=1, cutoff=0.8)
        if close:
            return "library" if close[0] == "library" else "cafeteria"
    return None


_ROOM_NO = re.compile(r"(?<![a-z])(?:classroom|class|room|cr|lecture hall)s?\s*(?:no\.?|#|number)?\s*-?\s*(\d{1,3})(?!\d)",
                      re.IGNORECASE)


def room_number(text: str) -> Optional[str]:
    m = _ROOM_NO.search(text.lower())
    return m.group(1) if m else None


def place_key_and_name(place_id: str, text: str):
    """Classrooms keep their room number: ('cse-classroom-5', 'CSE Classroom 5')."""
    place = PLACE_BY_ID[place_id]
    if place.get("kind") == "classroom":
        n = room_number(text)
        if n:
            return f"{place_id}-{n}", f"{place['name']} {n}"
    return place_id, place["name"]


def mentions_classroom(text: str) -> bool:
    return bool(re.search(r"(?<![a-z])(classroom|class room|lecture hall|\bcr\b)", text.lower()))


def unknown_numbered_mention(text: str, matched_place_id: Optional[str] = None) -> Optional[str]:
    """A numbered place that is not on the campus map, e.g. 'Lab 99' or 'Room 404'."""
    matched = PLACE_BY_ID.get(matched_place_id or "")
    for m in _NUMBERED_MENTION.finditer(text.lower()):
        mention = re.sub(r"\s+", " ", m.group(1)).strip().replace("- ", "").replace("-", " ")
        mention = re.sub(r"(lab|room|hall|block|floor)(\d)", r"\1 \2", mention)
        if mention not in _KNOWN_LAB_MENTIONS:
            # "cse room 12" is a numbered CSE classroom, not an unknown place
            if matched and matched.get("kind") == "classroom" and re.match(r"((cse|cs|ee|is)\s*)?room", mention):
                continue
            return mention
    return None


# Places that are clearly not on this campus (fictional, other cities, chains). If one is named, the report
# is not routed even if it also says "library" or "lab" (rule 6: no guessing).
_NOT_ON_CAMPUS = re.compile(
    r"(?<![a-z])(hogwarts|narnia|atlantis|mordor|wakanda|gotham|neverland|mars|moon|jupiter|starbucks|kfc|mcdonald'?s?|"
    r"pizza hut|subway station|airport|railway station|harvard|oxford|stanford|mit campus|lums|fast university|comsats|"
    r"giki|another campus|other campus|my house|my home)(?![a-z])",
    re.IGNORECASE,
)


def off_campus_mention(text: str) -> Optional[str]:
    m = _NOT_ON_CAMPUS.search(text.lower())
    return m.group(1) if m else None


def suggest_places(mention: Optional[str], text: str = "") -> List[Dict]:
    if text and mentions_classroom(text):
        return [p for p in PLACES if p.get("kind") == "classroom"]  # only CSE, EE, IS have classrooms
    if not mention:
        return PLACES
    low = mention.lower()
    words = [w for w in re.findall(r"[a-z]+", low) if w not in {"lab", "room", "hall", "block", "floor"}]
    hits = [p for p in PLACES if any(w in p["name"].lower() for w in words)] if words else []
    if not hits and re.search(r"\blab\b", low):
        hits = [p for p in PLACES if "lab" in p["name"].lower()]
    return hits or PLACES
