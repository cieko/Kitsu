
from __future__ import annotations

from typing import Any

from app.database.repositories.translation_repository import (
    TranslationRepository,
    translation_repository,
)


class TranslationLearningService:
    """Manage translation corrections and approved training examples."""

    def __init__(
        self,
        repository: TranslationRepository | None = None,
    ) -> None:
        self.repository = (
            repository
            if repository is not None
            else translation_repository
        )

    def submit_correction(
        self,
        *,
        source_text: str,
        model_translation: str,
        corrected_translation: str,
        source_language: str,
        target_language: str,
        guild_id: int | str,
        channel_id: int | str,
        message_id: int | str,
        submitted_by: int | str,
        model_name: str,
        consent_confirmed: bool,
    ) -> dict[str, Any]:
        """Submit a correction for review, requiring explicit consent."""

        if not consent_confirmed:
            raise ValueError(
                "Explicit consent is required before submitting "
                "a translation example for potential model training."
            )

        if source_language.strip().lower() == target_language.strip().lower():
            raise ValueError(
                "Source and target languages must be different."
            )

        return self.repository.submit_feedback(
            source_text=source_text,
            model_translation=model_translation,
            corrected_translation=corrected_translation,
            source_language=source_language,
            target_language=target_language,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            submitted_by=submitted_by,
            model_name=model_name,
            consent_confirmed=True,
        )

    def get_pending_examples(
        self,
        *,
        limit: int = 50,
        guild_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """Return examples awaiting review."""

        return self.repository.get_pending(
            limit=limit,
            guild_id=str(guild_id) if guild_id is not None else None,
        )

    def review_example(
        self,
        *,
        example_id: str,
        approved: bool,
        reviewed_by: int | str,
        review_note: str | None = None,
    ) -> bool:
        """Approve or reject a pending example."""

        if not example_id.strip():
            raise ValueError("Example ID cannot be empty.")

        if reviewed_by is None or not str(reviewed_by).strip():
            raise ValueError("Reviewer ID is required.")

        if review_note is not None:
            review_note = review_note.strip() or None

        return self.repository.review_example(
            example_id=example_id,
            approved=approved,
            reviewed_by=str(reviewed_by),
            review_note=review_note,
        )

    def get_approved_examples(
        self,
        *,
        limit: int = 10000,
        source_language: str | None = None,
        target_language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return consented, approved examples eligible for training."""

        return self.repository.get_approved(
            limit=limit,
            source_language=source_language,
            target_language=target_language,
        )

    def get_example(
        self,
        example_id: str,
    ) -> dict[str, Any] | None:
        """Retrieve an example by its ID."""

        if not example_id.strip():
            raise ValueError("Example ID cannot be empty.")

        return self.repository.get_by_id(example_id)

    def delete_example(
        self,
        example_id: str,
    ) -> bool:
        """Delete an example from the learning dataset."""

        if not example_id.strip():
            raise ValueError("Example ID cannot be empty.")

        return self.repository.delete_example(example_id)


translation_learning_service = TranslationLearningService()
