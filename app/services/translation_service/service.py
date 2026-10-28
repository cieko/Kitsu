from __future__ import annotations

import asyncio
import threading

import torch
from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

from .constants import LANGUAGE_CODES, MODEL_NAME, logger
from .detection import detect_language, transliterate_romanized
from .models import DetectionResult, TranslationResult


class TranslationService:
    """Lazy-loaded M2M100 translation service."""

    def __init__(self, model_name: str = MODEL_NAME):
        self.model_name = model_name
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
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

            tokenizer = M2M100Tokenizer.from_pretrained(self.model_name)

            dtype = torch.float16 if self.device.type == "cuda" else torch.float32

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
            raise ValueError(f"Unsupported M2M100 source language: {source_code}")

        if target_code not in self._tokenizer.lang_code_to_id:
            raise ValueError(f"Unsupported M2M100 target language: {target_code}")

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
                    forced_bos_token_id=(self._tokenizer.get_lang_id(target_code)),
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
            raise ValueError(f"Unsupported target language: {target_language!r}")

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
