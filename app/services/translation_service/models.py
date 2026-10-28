from __future__ import annotations

from dataclasses import dataclass

from .constants import LANGUAGE_CODES, MODEL_NAME


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
