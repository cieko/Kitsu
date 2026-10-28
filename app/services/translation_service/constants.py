from __future__ import annotations

import logging
import re
from pathlib import Path

from lingua import Language

logger = logging.getLogger(__name__)


MODEL_NAME = "facebook/m2m100_418M"


PROJECT_ROOT = Path(__file__).resolve().parents[3]


VOCABULARY_PATH = PROJECT_ROOT / "data" / "translation_language_data.json"


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
