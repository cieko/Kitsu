"""Translation public interface."""

from .cog import TranslationCog as TranslationCog
from .cog import setup as setup
from .commands import TranslationCommands as TranslationCommands
from .configuration import TranslationConfiguration as TranslationConfiguration
from .constants import CONFIG_COLLECTION as CONFIG_COLLECTION
from .constants import MAX_INPUT_LENGTH as MAX_INPUT_LENGTH
from .constants import MAX_TRANSLATION_LENGTH as MAX_TRANSLATION_LENGTH
from .constants import SERVER_DEFAULT_ID as SERVER_DEFAULT_ID
from .constants import SUPPORTED_LANGUAGES as SUPPORTED_LANGUAGES
from .constants import logger as logger
from .groups import translation as translation
from .helpers import clean_message as clean_message
from .helpers import language_autocomplete as language_autocomplete
from .helpers import resolve_language as resolve_language

__all__ = [
    "CONFIG_COLLECTION",
    "MAX_INPUT_LENGTH",
    "MAX_TRANSLATION_LENGTH",
    "SERVER_DEFAULT_ID",
    "SUPPORTED_LANGUAGES",
    "TranslationCog",
    "TranslationCommands",
    "TranslationConfiguration",
    "clean_message",
    "language_autocomplete",
    "logger",
    "resolve_language",
    "setup",
    "translation",
]
