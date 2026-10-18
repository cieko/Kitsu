
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection

from app.database.session import db


TRANSLATION_EXAMPLES_COLLECTION = "translation_examples"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")

    value = value.strip()

    if not value:
        raise ValueError(f"{field_name} cannot be empty")

    if len(value) > 10_000:
        raise ValueError(f"{field_name} exceeds 10,000 characters")

    return value


class TranslationRepository:
    """MongoDB repository for translation feedback and training examples."""

    def __init__(
        self,
        collection: Collection | None = None,
    ) -> None:
        self.collection = (
            collection
            if collection is not None
            else db[TRANSLATION_EXAMPLES_COLLECTION]
        )

    def ensure_indexes(self) -> None:
        """Create indexes used by feedback and dataset-export workflows."""
        self.collection.create_index(
            [("example_id", ASCENDING)],
            unique=True,
            name="unique_translation_example_id",
        )
        self.collection.create_index(
            [
                ("status", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="translation_status_created",
        )
        self.collection.create_index(
            [
                ("guild_id", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="translation_guild_created",
        )
        self.collection.create_index(
            [
                ("source_language", ASCENDING),
                ("target_language", ASCENDING),
                ("status", ASCENDING),
            ],
            name="translation_language_pair_status",
        )

    def submit_feedback(
        self,
        *,
        source_text: str,
        model_translation: str,
        corrected_translation: str,
        source_language: str,
        target_language: str,
        guild_id: int | str,
        channel_id: int | str | None = None,
        message_id: int | str | None = None,
        submitted_by: int | str | None = None,
        model_name: str | None = None,
        consent_confirmed: bool = False,
    ) -> dict[str, Any]:
        """
        Save a correction for review.

        The caller must obtain appropriate consent before saving message
        content for future training. Corrections remain pending until reviewed.
        """

        if not consent_confirmed:
            raise ValueError(
                "Explicit consent is required to save text for training."
            )

        source_text = _clean_text(source_text, "source_text")
        model_translation = _clean_text(
            model_translation,
            "model_translation",
        )
        corrected_translation = _clean_text(
            corrected_translation,
            "corrected_translation",
        )
        source_language = _clean_text(
            source_language,
            "source_language",
        ).lower()
        target_language = _clean_text(
            target_language,
            "target_language",
        ).lower()

        now = _utc_now()

        document: dict[str, Any] = {
            "example_id": str(uuid4()),
            "source_text": source_text,
            "model_translation": model_translation,
            "corrected_translation": corrected_translation,
            "source_language": source_language,
            "target_language": target_language,
            "guild_id": str(guild_id),
            "channel_id": str(channel_id) if channel_id is not None else None,
            "message_id": str(message_id) if message_id is not None else None,
            "submitted_by": str(submitted_by) if submitted_by is not None else None,
            "model_name": model_name,
            "status": "pending",
            "consent_confirmed": True,
            "created_at": now,
            "reviewed_at": None,
            "reviewed_by": None,
            "review_note": None,
        }

        self.collection.insert_one(document)

        return document

    def review_example(
        self,
        example_id: str,
        *,
        approved: bool,
        reviewed_by: int | str,
        review_note: str | None = None,
    ) -> bool:
        """Approve or reject a pending training example."""

        status = "approved" if approved else "rejected"

        result = self.collection.update_one(
            {
                "example_id": example_id,
                "status": "pending",
            },
            {
                "$set": {
                    "status": status,
                    "reviewed_at": _utc_now(),
                    "reviewed_by": str(reviewed_by),
                    "review_note": review_note,
                }
            },
        )

        return result.modified_count == 1

    def get_pending(
        self,
        *,
        limit: int = 50,
        guild_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """Return pending examples, oldest first."""

        limit = max(1, min(limit, 200))

        query: dict[str, Any] = {"status": "pending"}

        if guild_id is not None:
            query["guild_id"] = str(guild_id)

        return list(
            self.collection.find(query, {"_id": 0})
            .sort("created_at", ASCENDING)
            .limit(limit)
        )

    def get_approved(
        self,
        *,
        limit: int = 10_000,
        source_language: str | None = None,
        target_language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return approved examples for evaluation or dataset export."""

        limit = max(1, min(limit, 100_000))

        query: dict[str, Any] = {
            "status": "approved",
            "consent_confirmed": True,
        }

        if source_language:
            query["source_language"] = source_language.lower()

        if target_language:
            query["target_language"] = target_language.lower()

        return list(
            self.collection.find(query, {"_id": 0})
            .sort("created_at", ASCENDING)
            .limit(limit)
        )

    def get_by_id(self, example_id: str) -> dict[str, Any] | None:
        """Retrieve one example by its public example ID."""

        return self.collection.find_one(
            {"example_id": example_id},
            {"_id": 0},
        )

    def delete_example(self, example_id: str) -> bool:
        """Delete an example by ID, including an already-approved example."""

        result = self.collection.delete_one(
            {"example_id": example_id},
        )

        return result.deleted_count == 1


translation_repository = TranslationRepository()
