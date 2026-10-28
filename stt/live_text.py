"""Small language-aware corrections for live dictation segments."""
from __future__ import annotations

import re


_CONTINUATION_WORDS = {
    "en": {"and", "but", "because", "that", "which", "so", "or", "if", "when"},
    "it": {"e", "ma", "perché", "che", "quindi", "oppure", "se", "quando",
           "però", "allora", "invece", "poi"},
    "de": {"und", "aber", "weil", "dass", "also", "oder", "wenn"},
    "fr": {"et", "mais", "parce", "que", "donc", "ou", "si", "quand"},
    "es": {"y", "pero", "porque", "que", "así", "o", "si", "cuando"},
    "pt": {"e", "mas", "porque", "que", "então", "ou", "se", "quando"},
    "nl": {"en", "maar", "omdat", "dat", "dus", "of", "als", "wanneer"},
    "no": {"og", "men", "fordi", "at", "så", "eller", "hvis", "når"},
    "sv": {"och", "men", "eftersom", "att", "så", "eller", "om", "när"},
    "da": {"og", "men", "fordi", "at", "så", "eller", "hvis", "når"},
}

_CONTINUATION_STARTERS = {
    "en": {"and", "but", "because", "that", "which", "so", "or", "if", "when",
           "to", "of", "for", "with", "from", "in", "on", "as", "while"},
    "it": {"e", "ma", "perché", "che", "quindi", "oppure", "se", "quando",
           "però", "allora", "invece", "poi", "di", "da", "in", "a", "ad",
           "per", "con", "su", "tra", "fra", "come", "mentre", "finché"},
    "de": {"und", "aber", "weil", "dass", "also", "oder", "wenn", "zu", "von",
           "für", "mit", "in", "auf", "während"},
    "fr": {"et", "mais", "parce", "que", "donc", "ou", "si", "quand", "de",
           "du", "des", "pour", "avec", "dans", "sur", "pendant"},
    "es": {"y", "pero", "porque", "que", "así", "o", "si", "cuando", "de",
           "del", "para", "con", "en", "por", "mientras"},
    "pt": {"e", "mas", "porque", "que", "então", "ou", "se", "quando", "de",
           "do", "da", "para", "com", "em", "por", "enquanto"},
    "nl": {"en", "maar", "omdat", "dat", "dus", "of", "als", "wanneer", "van",
           "voor", "met", "in", "op", "terwijl"},
    "no": {"og", "men", "fordi", "at", "så", "eller", "hvis", "når", "av",
           "for", "med", "i", "på", "mens"},
    "sv": {"och", "men", "eftersom", "att", "så", "eller", "om", "när", "av",
           "för", "med", "i", "på", "medan"},
    "da": {"og", "men", "fordi", "at", "så", "eller", "hvis", "når", "af",
           "for", "med", "i", "på", "mens"},
}
_LOWERCASE_CONTINUATION_STARTERS = {
    language: starters | ({"un", "una", "uno"} if language == "it" else set())
    for language, starters in _CONTINUATION_STARTERS.items()
}


def likely_continues_sentence(previous: str, following: str, language: str) -> bool:
    """Detect clear clause continuations when the local LLM cannot classify a boundary."""
    following = following.lstrip()
    if not previous.strip() or not following:
        return False

    first_word = re.match(r"\w+", following.casefold())
    if first_word and first_word.group() in _CONTINUATION_STARTERS.get(language, set()):
        return True

    words = re.findall(r"\w+", previous.casefold())
    if (
        language == "it"
        and words in (["oh"], ["ah"], ["eh"])
        and first_word
        and first_word.group() in {"un", "una", "uno"}
    ):
        return True
    return bool(words and words[-1] in _CONTINUATION_WORDS.get(language, set()))


def adjust_sentence_start(text: str, *, capitalize: bool, language: str) -> str:
    """Change the first letter after a sentence boundary without touching spacing."""
    first_word = re.search(r"\w+", text)
    if not capitalize and (
        first_word is None
        or first_word.group().casefold()
        not in _LOWERCASE_CONTINUATION_STARTERS.get(language, set())
    ):
        return text
    for index, char in enumerate(text):
        if not char.isalpha():
            continue
        if capitalize:
            replacement = char.upper()
        else:
            if language == "en" and text[index:index + 2].casefold() == "i ":
                return text
            replacement = char.lower()
        return text[:index] + replacement + text[index + 1:]
    return text


def ends_sentence(text: str) -> bool:
    text = text.rstrip()
    return bool(text) and text[-1] in ".?!。？！؟"


def continuation_separator(previous: str, language: str) -> str:
    if language == "it" and previous.strip().casefold() in {"oh.", "ah.", "eh."}:
        return ", "
    return " "
