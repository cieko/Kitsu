from __future__ import annotations

import logging

from app.services.translation_service import LANGUAGE_CODES

logger = logging.getLogger(__name__)


CONFIG_COLLECTION = "translation_channel_configs"


MAX_TRANSLATION_LENGTH = 1900


MAX_INPUT_LENGTH = 5000


SERVER_DEFAULT_ID = "server-default"


SUPPORTED_LANGUAGES = {language.casefold(): language for language in LANGUAGE_CODES}
