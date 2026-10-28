from __future__ import annotations

import re
from functools import lru_cache

from lingua import LanguageDetectorBuilder

from .constants import LANGUAGE_CODES, LINGUA_LANGUAGES, WORD_PATTERN
from .models import DetectionResult
from .vocabulary import vocabulary_store


@lru_cache(maxsize=1)
def _get_detector():
    return (
        LanguageDetectorBuilder.from_languages(*LINGUA_LANGUAGES)
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
        sufficiently_separated = best_score - second_score >= 0.75

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
                    entry.language == "English" and not entry.ambiguous
                    for entry in vocabulary_store.entries_for_spelling(token)
                )
            )

            if english_hits >= 2:
                return DetectionResult(
                    "Mixed Hindi-English" if best_language == "Hindi" else "Mixed Bengali-English",
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
        (item.value for item in confidence_values if item.language == detected),
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
