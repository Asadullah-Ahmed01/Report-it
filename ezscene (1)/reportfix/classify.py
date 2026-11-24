"""Code-side detectors and the keyword fallback classifier (English + Roman Urdu).

These run on every report, with or without AI:
- detect_injection: report text trying to command the system (rule 4)
- detect_hazard: safety keyword floor, so a fire is never missed (rule 3)
- detect_confidential: harassment / ragging keyword floor (rule 5)
- tone_clean: strips shouting and urgency words before the AI sees the text (rule 3)
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from reportfix.places import PLACE_BY_ID, match_place, off_campus_mention, unknown_numbered_mention


def _rx(patterns: List[str]) -> re.Pattern:
    return re.compile(r"(?<![a-z])(?:" + "|".join(patterns) + r")(?![a-z])", re.IGNORECASE)


HAZARD_RX: Dict[str, re.Pattern] = {
    "fire": _rx([r"fire", r"aag", r"smoke", r"dhuan", r"dhuwan", r"dhuwaan", r"burning", r"burnt smell",
                 r"burning smell", r"flames?", r"jal (?:raha|rahi|rahe|gaya|gayi)"]),
    "sparks": _rx([r"sparks?", r"sparking", r"chingari\w*", r"short ?circuit\w*"]),
    "electrical": _rx([r"electric(?:al)? shock", r"shock (?:lag|laga|lagi)\w*", r"current (?:lag|laga|lagi|aa|maar)\w*",
                       r"electrocut\w*", r"live wires?", r"exposed wires?", r"naked wires?", r"nangi (?:taar|tar)\w*"]),
    "gas": _rx([r"gas", r"gas leak\w*", r"cylinder leak\w*"]),
    "medical": _rx([r"faint(?:ed|ing)?", r"behosh\w*", r"unconscious", r"bleeding", r"khoon", r"blood",
                    r"injur(?:ed|y|ies)", r"zakhmi", r"seizure", r"heart attack", r"not breathing", r"saans nahi\w*",
                    r"ambulance", r"medical emergency", r"collapsed", r"chot (?:lagi|lag gayi|aayi)"]),
}

CONFIDENTIAL_RX = _rx([
    r"harass\w*", r"ragg?ing", r"ragged", r"bully\w*", r"bullie\w*", r"stalk\w*", r"blackmail\w*",
    r"threaten\w*", r"dhamki\w*", r"dhamka\w*", r"inappropriate(?:ly)? touch\w*", r"molest\w*", r"assault\w*",
    r"misbehav\w*", r"chhed\w*", r"chher\w*", r"tang (?:kar|karte|karta|karti|kiya)\w*",
    r"pareshan (?:kar|karte|karta|karti|kiya)\w*", r"follow (?:karta|karte|kar raha)\w*", r"gaali\w*",
])

INJECTION_RX = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(ignore|disregard|forget|bypass|override)\b.{0,40}\b(instruction|rule|prompt|guideline|system|polic|previous|above)",
        r"\b(system|assistant|developer|admin)\s*(prompt)?\s*:",
        r"\[\s*(system|admin|instruction)s?\s*\]",
        r"\byou are now\b|\bact as\b|\bpretend to be\b|\bnew instructions?\b",
        r"\b(mark|set|make|classify|flag|treat|label|escalate|route)\b.{0,30}\b(critical|urgent|high priority|top priority|highest priority|resolved|emergency|admin)\b",
        r"\b(priority|department|status|severity)\s*[:=]\s*\w+",
        r"\b(critical|urgent|top priority|high priority)\s+(mark|bana|kar|set|kardo|kar do)\b",
        r"\bisko\b.{0,20}\b(critical|urgent|priority)\b",
    ]
]

URGENCY_RX = re.compile(
    r"(?<![a-z])(urgent(?:ly)?|asap|immediately|right now|foran|fori|jaldi|jldi|critical|top priority|"
    r"high priority|highest priority|very important|plz+|please+|(?<!medical )emergency)(?![a-z])",
    re.IGNORECASE,
)

CATEGORY_RX: Dict[str, re.Pattern] = {
    "wifi": _rx([r"wi-?fi", r"wfi", r"internet", r"net", r"network", r"router", r"signals?", r"lan", r"ethernet", r"connectivity"]),
    "ac": _rx([r"ac", r"a/c", r"aircon\w*", r"air ?condition\w*", r"cooling", r"heater", r"heating", r"garmi"]),
    "fan": _rx([r"fans?", r"pankh\w*", r"ceiling fans?", r"exhaust fans?"]),
    "projector": _rx([r"projector\w*", r"projecter", r"projctor", r"projctr", r"multimedia", r"hdmi", r"screen"]),
    "computers": _rx([r"pcs?", r"computers?", r"systems?", r"keyboards?", r"mouse", r"monitors?", r"printer\w*", r"lms", r"portal", r"login", r"software"]),
    "electrical": _rx([r"power", r"bijli", r"lights?", r"sockets?", r"switch\w*", r"wir(?:e|es|ing)", r"ups", r"generator", r"voltage",
                       r"outage", r"load ?shedding", r"electricity"]),
    "water": _rx([r"water", r"pani", r"paani", r"leak\w*", r"taps?", r"washrooms?", r"toilets?", r"sinks?", r"cooler", r"flush"]),
    "cleanliness": _rx([r"dirty", r"ganda", r"gandi", r"safai", r"garbage", r"trash", r"kachra", r"cockroach\w*", r"rats?", r"dust\w*", r"filthy", r"smell\w*", r"badboo"]),
    "furniture": _rx([r"chairs?", r"tables?", r"desks?", r"benche?s?", r"doors?", r"windows?", r"kursi\w*", r"mez", r"darwaz\w*", r"shelf", r"shelves", r"seats?"]),
    "food": _rx([r"food", r"khana", r"tea", r"chai", r"spoiled", r"basi", r"stale", r"menu", r"biryani", r"roti", r"samosa\w*", r"burger", r"fries", r"meal", r"lunch", r"hygiene"]),
}

CATEGORY_TITLES = {
    "wifi": "WiFi / internet not working",
    "ac": "AC / cooling problem",
    "fan": "Fan not working",
    "projector": "Projector not working",
    "computers": "Computer / PC problem",
    "electrical": "Electrical / power problem",
    "water": "Water / washroom problem",
    "cleanliness": "Cleanliness problem",
    "furniture": "Broken furniture",
    "food": "Food / hygiene problem",
    "safety": "Safety hazard",
    "harassment": "Confidential welfare report",
    "other": "General campus issue",
}


def detect_injection(text: str) -> bool:
    return any(rx.search(text) for rx in INJECTION_RX)


def strip_injection(text: str) -> str:
    out = text
    for rx in INJECTION_RX:
        out = rx.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip()


def detect_hazard(text: str) -> Optional[str]:
    for kind, rx in HAZARD_RX.items():
        if rx.search(text):
            return kind
    return None


def detect_confidential(text: str) -> bool:
    return bool(CONFIDENTIAL_RX.search(text))


def is_shouting(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    caps = sum(1 for c in letters if c.isupper())
    return (len(letters) >= 8 and caps / len(letters) > 0.6) or "!!" in text


def tone_clean(text: str) -> str:
    """Remove tone so priority can't be shouted up: lowercase, no '!!!', no urgency words."""
    out = text.replace("<<<", " ").replace(">>>", " ")
    out = re.sub(r"[!]{1,}", ".", out)
    out = re.sub(r"[?]{2,}", "?", out)
    out = out.lower()
    out = URGENCY_RX.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip(" .")


def detect_category(text: str) -> str:
    # earliest strong signal wins; projector/wifi/ac before generic words like "screen"/"system"
    order = ["projector", "wifi", "ac", "fan", "water", "electrical", "computers", "food", "furniture", "cleanliness"]
    for cat in order:
        if CATEGORY_RX[cat].search(text):
            return cat
    return "other"


def fallback_triage(text: str) -> Dict:
    """Keyword classifier: used when Gemini is unavailable. Same output shape as the AI."""
    data_only = strip_injection(text)
    hazard = detect_hazard(data_only)
    confidential = detect_confidential(data_only)
    place_id = match_place(data_only)
    unknown = unknown_numbered_mention(data_only, place_id) or off_campus_mention(data_only)
    if unknown:
        place_id = None
    category = "harassment" if confidential else detect_category(data_only)
    if hazard and category == "other":
        category = "safety"
    place = PLACE_BY_ID.get(place_id) if place_id else None
    title = f"Safety hazard: {hazard}" if hazard else CATEGORY_TITLES.get(category, "General campus issue")
    if confidential:
        reason = "Mentions harassment or ragging, so it goes to the confidential admin queue."
    elif hazard:
        reason = f"Keyword match for a {hazard} hazard" + (f" at {place['name']}" if place else "") + "."
    elif place:
        reason = f"Keyword match for {category} at {place['name']}."
    else:
        reason = f"Keyword match for {category}; no campus place recognised."
    return {
        "place_id": place_id,
        "place_mention": unknown,
        "category": category,
        "hazard": hazard,
        "confidential": confidential,
        "injection": detect_injection(text),
        "same_as_issue_id": None,
        "summary": title,
        "reason": reason,
        "source": "fallback",
    }
