
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import torch
from lingua import Language, LanguageDetectorBuilder
from transformers import (
    M2M100ForConditionalGeneration,
    M2M100Tokenizer,
)

logger = logging.getLogger(__name__)

MODEL_NAME = "facebook/m2m100_418M"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VOCABULARY_PATH = PROJECT_ROOT / "data" / "translation_language_data.json"

# M2M100 language compatibility configuration.
# This is model configuration, not vocabulary.
LANGUAGE_CODES = {
    "Arabic": "ar",
    "Bengali": "bn",
    "Chinese": "zh",
    "Dutch": "nl",
    "English": "en",
    "French": "fr",
    "German": "de",
    "Hindi": "hi",
    "Indonesian": "id",
    "Italian": "it",
    "Japanese": "ja",
    "Korean": "ko",
    "Malay": "ms",
    "Persian": "fa",
    "Polish": "pl",
    "Portuguese": "pt",
    "Russian": "ru",
    "Spanish": "es",
    "Swahili": "sw",
    "Thai": "th",
    "Turkish": "tr",
    "Ukrainian": "uk",
    "Urdu": "ur",
    "Vietnamese": "vi",
}

# Lingua detection configuration is separate from vocabulary.
LINGUA_LANGUAGES = [
    Language.ENGLISH,
    Language.HINDI,
    Language.BENGALI,
    Language.JAPANESE,
    Language.FRENCH,
    Language.SPANISH,
    Language.GERMAN,
]

WORD_PATTERN = re.compile(r"[A-Za-z]+")
TOKEN_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
SUPPORTED_VOCABULARY_LANGUAGES = set(LANGUAGE_CODES)


@dataclass(frozen=True)
class DetectionResult:
    language: str
    confidence: float
    reason: str
    is_romanized: bool = False
    is_mixed: bool = False

    @property
    def language_code(self) -> str | None:
        if self.is_mixed:
            return None

        return LANGUAGE_CODES.get(self.language)


@dataclass(frozen=True)
class TranslationResult:
    source_text: str
    translated_text: str
    source_language: str
    source_code: str
    target_language: str
    target_code: str
    confidence: float
    model_name: str = MODEL_NAME


@dataclass(frozen=True)
class VocabularyEntry:
    id: int
    language: str
    romanized: str
    native_script: str
    variants: tuple[str, ...]
    enabled: bool
    ambiguous: bool
    notes: str = ""

    @property
    def spellings(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                spelling.casefold()
                for spelling in (
                    self.romanized,
                    *self.variants,
                )
                if spelling.strip()
            )
        )


class VocabularyStore:
    """Loads vocabulary from JSON and refreshes when the file changes."""

    def __init__(self, path: Path = VOCABULARY_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._mtime_ns: int | None = None
        self._entries: tuple[VocabularyEntry, ...] = ()
        self._by_language: dict[str, tuple[VocabularyEntry, ...]] = {}
        self._by_spelling: dict[
            str, tuple[VocabularyEntry, ...]
        ] = {}

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
            raise ValueError(
                "Vocabulary JSON must contain an 'entries' list."
            )

        entries: list[VocabularyEntry] = []

        for item in raw_entries:
            if not isinstance(item, dict):
                continue

            if not item.get("enabled", True):
                continue

            language = str(item.get("language", "")).strip().title()
            romanized = str(item.get("romanized", "")).strip()
            native_script = str(
                item.get("native_script", "")
            ).strip()

            if (
                language not in SUPPORTED_VOCABULARY_LANGUAGES
                or not romanized
                or not native_script
            ):
                continue

            raw_variants = item.get("variants", [])

            if isinstance(raw_variants, str):
                variants = tuple(
                    value.strip()
                    for value in raw_variants.split(",")
                    if value.strip()
                )
            elif isinstance(raw_variants, list):
                variants = tuple(
                    str(value).strip()
                    for value in raw_variants
                    if str(value).strip()
                )
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
                by_language.setdefault(
                    entry.language, []
                ).append(entry)

                for spelling in entry.spellings:
                    by_spelling.setdefault(
                        spelling, []
                    ).append(entry)

            self._entries = entries
            self._by_language = {
                language: tuple(values)
                for language, values in by_language.items()
            }
            self._by_spelling = {
                spelling: tuple(values)
                for spelling, values in by_spelling.items()
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


@lru_cache(maxsize=1)
def _get_detector():
    return (
        LanguageDetectorBuilder
        .from_languages(*LINGUA_LANGUAGES)
        .with_preloaded_language_models()
        .build()
    )


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in WORD_PATTERN.findall(text)]


def _script_language(text: str) -> str | None:
    """Identify languages with distinctive writing systems."""
    if re.search(r"[\u0900-\u097F]", text):
        return "Hindi"

    if re.search(r"[\u0980-\u09FF]", text):
        return "Bengali"

    # Japanese kana or CJK characters.
    if re.search(r"[\u3040-\u30FF]", text):
        return "Japanese"

    # Chinese characters without Japanese kana.
    if re.search(r"[\u4E00-\u9FFF]", text):
        return "Chinese"

    if re.search(r"[\u0600-\u06FF]", text):
        return "Arabic"

    if re.search(r"[\uAC00-\uD7AF]", text):
        return "Korean"

    if re.search(r"[\u0E00-\u0E7F]", text):
        return "Thai"

    return None


def _romanized_scores(
    tokens: list[str],
) -> dict[str, tuple[float, int, int]]:
    """
    Return language -> (score, strong hits, ambiguous hits).

    Vocabulary is sourced from JSON, not hardcoded language word lists.
    """
    scores: dict[str, list[float | int]] = {}

    for token in set(tokens):
        entries = vocabulary_store.entries_for_spelling(token)

        for entry in entries:
            if entry.language not in {"Hindi", "Bengali"}:
                continue

            bucket = scores.setdefault(
                entry.language,
                [0.0, 0, 0],
            )

            if entry.ambiguous:
                bucket[0] += 0.25
                bucket[2] += 1
            else:
                bucket[0] += 1.0
                bucket[1] += 1

    return {
        language: (float(values[0]), int(values[1]), int(values[2]))
        for language, values in scores.items()
    }


def detect_language(text: str) -> DetectionResult:
    """Detect a supported language, including configured Romanized Hindi/Bengali."""

    text = text.strip()

    if not text:
        return DetectionResult("Unknown", 0.0, "Empty message")

    script_language = _script_language(text)

    if script_language is not None:
        if script_language in LANGUAGE_CODES:
            return DetectionResult(
                script_language,
                0.99,
                "Writing-system detection",
            )

    tokens = _tokens(text)

    if not tokens:
        return DetectionResult(
            "Unknown",
            0.0,
            "No recognizable words",
        )

    scores = _romanized_scores(tokens)

    if scores:
        ranked = sorted(
            scores.items(),
            key=lambda item: item[1][0],
            reverse=True,
        )

        best_language, (best_score, strong_hits, _) = ranked[0]
        second_score = ranked[1][1][0] if len(ranked) > 1 else 0.0

        # Avoid guessing when the same spelling strongly matches
        # multiple languages.
        sufficiently_separated = (
            best_score - second_score >= 0.75
        )

        if strong_hits >= 2 and sufficiently_separated:
            confidence = min(
                0.96,
                0.65 + 0.07 * strong_hits,
            )

            # Mark mixed language if English vocabulary is also a
            # substantial part of the message.
            english_hits = sum(
                1
                for token in set(tokens)
                if any(
                    entry.language == "English"
                    and not entry.ambiguous
                    for entry in vocabulary_store.entries_for_spelling(
                        token
                    )
                )
            )

            if english_hits >= 2:
                return DetectionResult(
                    "Mixed Hindi-English"
                    if best_language == "Hindi"
                    else "Mixed Bengali-English",
                    confidence,
                    "Romanized language and English vocabulary detected",
                    is_romanized=True,
                    is_mixed=True,
                )

            return DetectionResult(
                best_language,
                confidence,
                "Romanized vocabulary match",
                is_romanized=True,
            )

        # A single distinctive token is weaker evidence.
        if strong_hits == 1 and len(tokens) <= 4:
            return DetectionResult(
                best_language,
                0.65,
                "One distinctive Romanized vocabulary match",
                is_romanized=True,
            )

    # Fall back to Lingua for languages written in Latin script.
    detector = _get_detector()
    detected = detector.detect_language_of(text)

    if detected is None:
        return DetectionResult(
            "Unknown",
            0.0,
            "Language could not be determined",
        )

    confidence_values = detector.compute_language_confidence_values(text)
    confidence = next(
        (
            item.value
            for item in confidence_values
            if item.language == detected
        ),
        0.0,
    )

    language_name = detected.name.title()

    if confidence < 0.45:
        return DetectionResult(
            "Unknown",
            confidence,
            f"Low-confidence result: {language_name}",
        )

    if language_name not in LANGUAGE_CODES:
        return DetectionResult(
            "Unknown",
            confidence,
            f"Translation code unavailable for {language_name}",
        )

    return DetectionResult(
        language_name,
        confidence,
        "Lingua detection",
    )


def transliterate_romanized(
    text: str,
    language: str,
) -> str:
    """Generic data-driven transliteration for supported vocabularies."""
    return vocabulary_store.transliterate(text, language)


class TranslationService:
    """Lazy-loaded M2M100 translation service."""

    def __init__(self, model_name: str = MODEL_NAME):
        self.model_name = model_name
        self.device = torch.device(
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        self._tokenizer = None
        self._model = None
        self._model_lock = threading.Lock()
        self._generation_lock = threading.Lock()

    def _load_model(self) -> None:
        if self._model is not None:
            return

        with self._model_lock:
            if self._model is not None:
                return

            logger.info(
                "Loading translation model %s on %s",
                self.model_name,
                self.device,
            )

            tokenizer = M2M100Tokenizer.from_pretrained(
                self.model_name
            )

            dtype = (
                torch.float16
                if self.device.type == "cuda"
                else torch.float32
            )

            model = M2M100ForConditionalGeneration.from_pretrained(
                self.model_name,
                torch_dtype=dtype,
            )

            model.to(self.device)
            model.eval()

            self._tokenizer = tokenizer
            self._model = model

            logger.info("Translation model loaded successfully")

    def _translate_sync(
        self,
        text: str,
        source_code: str,
        target_code: str,
    ) -> str:
        self._load_model()

        assert self._tokenizer is not None
        assert self._model is not None

        if source_code == target_code:
            return text

        if source_code not in self._tokenizer.lang_code_to_id:
            raise ValueError(
                f"Unsupported M2M100 source language: {source_code}"
            )

        if target_code not in self._tokenizer.lang_code_to_id:
            raise ValueError(
                f"Unsupported M2M100 target language: {target_code}"
            )

        with self._generation_lock:
            self._tokenizer.src_lang = source_code

            inputs = self._tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=512,
            ).to(self.device)

            with torch.inference_mode():
                output = self._model.generate(
                    **inputs,
                    forced_bos_token_id=(
                        self._tokenizer.get_lang_id(target_code)
                    ),
                    max_new_tokens=256,
                )

            translated = self._tokenizer.batch_decode(
                output,
                skip_special_tokens=True,
            )[0]

        return translated.strip()

    async def translate(
        self,
        text: str,
        target_language: str,
        *,
        detection: DetectionResult | None = None,
    ) -> TranslationResult | None:
        """
        Translate text into the requested language.

        Returns None for uncertain/mixed messages or when the detected
        source language is already the target language.
        """
        text = text.strip()

        if not text:
            return None

        target_language = next(
            (
                name
                for name in LANGUAGE_CODES
                if name.casefold() == target_language.strip().casefold()
            ),
            None,
        )

        if target_language is None:
            raise ValueError(
                f"Unsupported target language: {target_language!r}"
            )

        target_code = LANGUAGE_CODES[target_language]
        detection = detection or detect_language(text)

        if detection.language_code is None:
            logger.debug(
                "Skipping translation: %s (%s)",
                detection.language,
                detection.reason,
            )
            return None

        if detection.confidence < 0.60:
            return None

        source_code = detection.language_code

        if source_code == target_code:
            return None

        translation_input = text

        if detection.is_romanized:
            translation_input = transliterate_romanized(
                text,
                detection.language,
            )

            logger.info(
                "Normalized Romanized %s: %r -> %r",
                detection.language,
                text,
                translation_input,
            )

        translated_text = await asyncio.to_thread(
            self._translate_sync,
            translation_input,
            source_code,
            target_code,
        )

        if not translated_text:
            return None

        return TranslationResult(
            source_text=text,
            translated_text=translated_text,
            source_language=detection.language,
            source_code=source_code,
            target_language=target_language,
            target_code=target_code,
            confidence=detection.confidence,
            model_name=self.model_name,
        )


translation_service = TranslationService()
