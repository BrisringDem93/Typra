# stt/voice_commands.py
"""
Voice command detection.
Runs over transcribed text before it is typed.
Returns either the original text, a modified version, or a special action.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class CommandResult:
    text: str             # Text to type (may be empty)
    delete_last: bool = False  # Trigger delete-last-segment action
    action: str = ""      # Special action name if any


# Command tables per language
# Format: {phrase: replacement_or_action}
_COMMANDS: dict[str, dict[str, str]] = {
    "en": {
        "new line":       "\n",
        "new paragraph":  "\n\n",
        "period":         ".",
        "full stop":      ".",
        "comma":          ",",
        "question mark":  "?",
        "exclamation mark": "!",
        "colon":          ":",
        "semicolon":      ";",
        "open bracket":   "(",
        "close bracket":  ")",
        "delete that":    "__DELETE__",
        "undo that":      "__DELETE__",
        "scratch that":   "__DELETE__",
    },
    "de": {
        "neue zeile":     "\n",
        "neuer absatz":   "\n\n",
        "punkt":          ".",
        "komma":          ",",
        "fragezeichen":   "?",
        "ausrufezeichen": "!",
        "doppelpunkt":    ":",
        "semikolon":      ";",
        "löschen":        "__DELETE__",
        "rückgängig":     "__DELETE__",
    },
    "fr": {
        "nouvelle ligne":   "\n",
        "nouveau paragraphe": "\n\n",
        "point":            ".",
        "virgule":          ",",
        "point d'interrogation": "?",
        "point d'exclamation":   "!",
        "deux points":      ":",
        "supprimer":        "__DELETE__",
        "effacer":          "__DELETE__",
    },
}

# Add more languages as aliases
for _code in ["es", "it", "pt", "nl", "pl", "ja", "zh", "ko"]:
    _COMMANDS.setdefault(_code, _COMMANDS["en"])


def process_commands(text: str, language: str = "en") -> CommandResult:
    """
    Check text for voice commands and return the appropriate action.
    Commands are only recognised when the entire utterance matches, so a
    sentence that merely starts with a command word ("Period pain…",
    "Löschen Sie…", "Scratch that itch…") is typed as dictated and never
    deletes anything.
    """
    clean = text.strip().lower().rstrip(".!?,")
    commands = _COMMANDS.get(language, _COMMANDS["en"])

    replacement = commands.get(clean)
    if replacement is None:
        return CommandResult(text=text)
    if replacement == "__DELETE__":
        return CommandResult(text="", delete_last=True, action="delete")
    return CommandResult(text=replacement)
