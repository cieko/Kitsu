from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .constants import (
    SUPPORTED_VOCABULARY_LANGUAGES,
    TOKEN_PATTERN,
    VOCABULARY_PATH,
    logger,
)
from .models import VocabularyEntry


class VocabularyStore:
    """Loads vocabulary from JSON and refreshes when the file changes."""

    def __init__(self, path: Path = VOCABULARY_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._mtime_ns: int | None = None
        self._entries: tuple[VocabularyEntry, ...] = ()
        self._by_language: dict[str, tuple[VocabularyEntry, ...]] = {}
        self._by_spelling: dict[str, tuple[VocabularyEntry, ...]] = {}

    def _read_file(self) -> tuple[VocabularyEntry, ...]:
        if not self.path.exists():
            logger.warning(
                "Translation vocabulary not found at %s. "
                "Romanized language detection is unavailable until "
                "the vocabulary is exported.",
                self.path,
            )
            return ()

        with self.path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        raw_entries = payload.get("entries", [])

        if not isinstance(raw_entries, list):
            raise ValueError("Vocabulary JSON must contain an 'entries' list.")

        entries: list[VocabularyEntry] = []

        for item in raw_entries:
            if not isinstance(item, dict):
                continue

            if not item.get("enabled", True):
                continue

            language = str(item.get("language", "")).strip().title()
            romanized = str(item.get("romanized", "")).strip()
            native_script = str(item.get("native_script", "")).strip()

            if language not in SUPPORTED_VOCABULARY_LANGUAGES or not romanized or not native_script:
                continue

            raw_variants = item.get("variants", [])

            if isinstance(raw_variants, str):
                variants = tuple(
                    value.strip() for value in raw_variants.split(",") if value.strip()
                )
            elif isinstance(raw_variants, list):
                variants = tuple(str(value).strip() for value in raw_variants if str(value).strip())
            else:
                variants = ()

            entries.append(
                VocabularyEntry(
                    id=int(item.get("id", 0)),
                    language=language,
                    romanized=romanized,
                    native_script=native_script,
                    variants=variants,
                    enabled=True,
                    ambiguous=bool(item.get("ambiguous", False)),
                    notes=str(item.get("notes", "")).strip(),
                )
            )

        return tuple(entries)

    def refresh_if_changed(self) -> None:
        with self._lock:
            try:
                mtime_ns = self.path.stat().st_mtime_ns
            except FileNotFoundError:
                mtime_ns = None

            if mtime_ns == self._mtime_ns:
                return

            try:
                entries = self._read_file()
            except (OSError, ValueError, json.JSONDecodeError):
                logger.exception(
                    "Failed to load translation vocabulary from %s",
                    self.path,
                )
                # Preserve the last valid vocabulary on a bad edit.
                if self._mtime_ns is not None:
                    return
                entries = ()

            by_language: dict[str, list[VocabularyEntry]] = {}
            by_spelling: dict[str, list[VocabularyEntry]] = {}

            for entry in entries:
                by_language.setdefault(entry.language, []).append(entry)

                for spelling in entry.spellings:
                    by_spelling.setdefault(spelling, []).append(entry)

            self._entries = entries
            self._by_language = {
                language: tuple(values) for language, values in by_language.items()
            }
            self._by_spelling = {
                spelling: tuple(values) for spelling, values in by_spelling.items()
            }
            self._mtime_ns = mtime_ns

            logger.info(
                "Loaded %d enabled vocabulary entries from %s",
                len(entries),
                self.path,
            )

    @property
    def entries(self) -> tuple[VocabularyEntry, ...]:
        self.refresh_if_changed()
        return self._entries

    def entries_for_language(
        self,
        language: str,
    ) -> tuple[VocabularyEntry, ...]:
        self.refresh_if_changed()
        return self._by_language.get(language, ())

    def entries_for_spelling(
        self,
        spelling: str,
    ) -> tuple[VocabularyEntry, ...]:
        self.refresh_if_changed()
        return self._by_spelling.get(spelling.casefold(), ())

    def transliterate(
        self,
        text: str,
        language: str,
    ) -> str:
        """Replace known Romanized tokens with native-script entries."""
        self.refresh_if_changed()

        replacements: dict[str, str] = {}

        for entry in self._by_language.get(language, ()):
            for spelling in entry.spellings:
                # Do not silently choose between conflicting entries.
                existing = replacements.get(spelling)

                if existing is not None and existing != entry.native_script:
                    replacements.pop(spelling, None)
                    continue

                replacements[spelling] = entry.native_script

        def replace_word(match: re.Match[str]) -> str:
            original = match.group(0)
            replacement = replacements.get(original.casefold())

            return replacement if replacement is not None else original

        return TOKEN_PATTERN.sub(replace_word, text)


vocabulary_store = VocabularyStore()
