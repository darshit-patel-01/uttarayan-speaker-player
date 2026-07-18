"""
Multi-language profanity filter for dedication messages.

Normalizes text (l33t speak, repeated chars, punctuation obfuscation)
and checks against a built-in word list covering English, Hindi, Gujarati,
and Spanish. Extend by adding words to profanity_words.txt (one per line).
"""
import os
import re
from typing import Set

_SUBSTITUTIONS = str.maketrans({
    "@": "a", "4": "a",
    "3": "e",
    "1": "i", "!": "i",
    "0": "o",
    "$": "s", "5": "s",
    "7": "t",
})

_BUILTIN_WORDS: Set[str] = {
    # English
    "fuck", "fucker", "fucking", "shit", "shitty", "asshole", "bitch",
    "bastard", "dick", "cock", "pussy", "cunt", "whore", "slut",
    "nigger", "nigga", "faggot", "fag", "retard", "retarded",
    "motherfucker", "bullshit", "piss", "wank", "twat", "prick",
    "douche", "jackass", "dumbass", "dipshit", "arsehole",
    # Hindi (transliterated)
    "madarchod", "bhenchod", "behenchod", "chutiya", "chutiye",
    "gaand", "gand", "lund", "bhosdi", "bhosadike", "bhosdike",
    "bhosdiwale", "randi", "harami", "haramkhor",
    "kamina", "kamine", "kaminey", "gandu", "chut", "lodu",
    "lavde", "lavda", "jhatu", "jhaatu", "tatti",
    # Hindi short forms
    "mc", "bc",
    # Gujarati (transliterated)
    "gando", "gandi", "gandu", "bhunglo", "chodyu", "ghelu",
    "saalu", "bhangi",
    # Spanish
    "puta", "mierda", "pendejo", "cabron", "cabrón", "joder",
    "verga", "culo", "chingar", "hijo de puta",
}


def _normalize(text: str) -> str:
    text = text.lower().translate(_SUBSTITUTIONS)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return text


def _load_custom_words() -> Set[str]:
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "profanity_words.txt"
    )
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {
                line.strip().lower()
                for line in f
                if line.strip() and not line.startswith("#")
            }
    except OSError:
        return set()


_all_words: Set[str] = _BUILTIN_WORDS | _load_custom_words()


def contains_profanity(text: str) -> bool:
    if not text:
        return False
    normalized = _normalize(text)
    words = normalized.split()
    for word in words:
        if word in _all_words:
            return True
    joined = normalized.replace(" ", "")
    for bad in _all_words:
        if len(bad) >= 4 and bad in joined:
            return True
    return False
