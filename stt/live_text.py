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


def likely_continues_sentence(previous: str, following: str, language: str) -> bool:
    """Conservative fallback used when the local LLM cannot classify a boundary."""
    following = following.lstrip()
    if not previous.strip() or not following:
        return False

    first_letter = next((char for char in following if char.isalpha()), "")
    if first_letter and first_letter.islower():
        return True

    words = re.findall(r"\w+", previous.casefold())
    return bool(words and words[-1] in _CONTINUATION_WORDS.get(language, set()))


def adjust_sentence_start(text: str, *, capitalize: bool, language: str) -> str:
    """Change the first letter after a sentence boundary without touching spacing."""
    first_word = re.search(r"\w+", text)
    if not capitalize and (
        first_word is None
        or first_word.group().casefold() not in _CONTINUATION_WORDS.get(language, set())
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
