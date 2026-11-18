"""Small language-aware corrections for live dictation segments."""
from __future__ import annotations

import difflib
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
    "it": {"e", "ed", "o", "ma", "perché", "che", "quindi", "oppure", "se", "quando",
           "però", "allora", "invece", "poi", "cioè", "di", "da", "in", "a", "ad",
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


def new_part(previous: str, combined: str) -> str:
    """The end of `combined` (previous phrase and new one transcribed together)
    that was not in `previous`, for when the previous phrase cannot be corrected
    on screen. Words are matched loosely: punctuation and case may differ."""
    words = list(re.finditer(r"\w+", combined))
    old = [w.casefold() for w in re.findall(r"\w+", previous)]
    new = [w.group().casefold() for w in words]
    if not old:
        return combined.strip()
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    end = 0   # words of `combined` that belong to the previous phrase
    for block in matcher.get_matching_blocks():
        if block.size:
            end = block.b + block.size
    if end >= len(words):
        return ""
    if end == 0:
        return combined.strip()
    # From the end of the last word of the previous phrase, skipping the
    # punctuation and spaces that separated the two phrases
    rest = combined[words[end - 1].end():]
    return re.sub(r"^[\s.,;:!?…]+", "", rest).strip()


# The same 1-6 words, four or more times in a row: Whisper stuck in a loop
_LOOP = re.compile(r"\b(\w+(?:\W+\w+){0,5}?)(?:\W+\1\b){3,}", re.IGNORECASE)


def has_loop(text: str) -> bool:
    """Whisper repeating the same words over and over ("con una tabella con
    una tabella ..."), which it does on some audio instead of transcribing it."""
    return bool(_LOOP.search(text))


def collapse_loops(text: str) -> str:
    """Keep one copy of words repeated in a loop."""
    return _LOOP.sub(lambda m: m.group(1), text)


def ends_sentence(text: str) -> bool:
    text = text.rstrip()
    return bool(text) and text[-1] in ".?!。？！؟"


def continuation_separator(previous: str, language: str) -> str:
    if language == "it" and previous.strip().casefold() in {"oh.", "ah.", "eh."}:
        return ", "
    return " "


# "apposto" is only right as the participle of "apporre" (ho apposto la firma,
# viene apposto il timbro): after any other word it is a mishearing of "a posto"
_APPOSTO = re.compile(r"\b([Aa])pposto\b")
_APPORRE_AUXILIARIES = {
    "ho", "hai", "ha", "abbiamo", "avete", "hanno", "avevo", "avevi", "aveva",
    "avevamo", "avevate", "avevano", "avrò", "avrai", "avrà", "avremo", "avrete",
    "avranno", "avrei", "avresti", "avrebbe", "avremmo", "avreste", "avrebbero",
    "abbia", "abbiano", "avessi", "avesse", "avessero", "avere", "aver", "avendo",
    "stato", "viene", "vengono", "venne", "verrà", "venga", "vengano", "veniva",
}


def fix_common_mistakes(text: str, language: str) -> str:
    """Correct frequent mishearings that Whisper and the cleanup model keep."""
    if language != "it":
        return text

    def _replace(match: re.Match) -> str:
        before = re.findall(r"\w+", text[:match.start()].casefold())
        if before and before[-1] in _APPORRE_AUXILIARIES:
            return match.group()
        return match.group(1) + " posto"

    return _APPOSTO.sub(_replace, text)
