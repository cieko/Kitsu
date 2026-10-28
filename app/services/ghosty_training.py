"""Versioned, atomic training snapshots stored inside the project data folder."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from app.services.ghosty_prediction import GhostyPredictor, utc


logger = logging.getLogger(__name__)
TRAINING_DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "ghosty"


class GhostyTrainingStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory if directory is not None else TRAINING_DATA_DIRECTORY

    @staticmethod
    def _examples(history: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
        examples = []
        for row in history:
            stamp = row.get("created_at")
            if (
                not isinstance(stamp, datetime) or utc(stamp) > utc(now)
                or row.get("outcome") not in {"hit", "miss"}
                or not str(row.get("channel_id", "")).isdigit()
            ):
                continue
            examples.append({
                "channel_id": str(row["channel_id"]), "outcome": row["outcome"],
                "created_at": utc(stamp).isoformat(),
                "result_message_id": str(row.get("result_message_id") or "0"),
            })
        examples.sort(key=lambda row: (row["created_at"], int(row["result_message_id"])))
        return examples

    @staticmethod
    def _read(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @classmethod
    def _write(cls, path: Path, payload: dict[str, Any]) -> None:
        if cls._read(path) == payload:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(payload, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load_or_train(
        self, guild_id: int, history: list[dict[str, Any]], *,
        now: datetime, enabled: bool, current: GhostyPredictor | None = None,
    ) -> GhostyPredictor:
        if type(guild_id) is not int or guild_id <= 0:
            raise ValueError("A positive server ID is required")
        examples = self._examples(history, now)
        encoded = json.dumps(examples, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprint = hashlib.sha256(encoded).hexdigest()
        directory = self.directory / str(guild_id)
        model_path = directory / "model.json"
        predictor = current
        if (
            predictor is None or predictor.training_fingerprint != fingerprint
            or predictor.patterns_enabled != enabled
        ):
            predictor = None
            saved = self._read(model_path)
            if isinstance(saved, dict) and saved.get("guild_id") == str(guild_id):
                try:
                    restored = GhostyPredictor.from_state(saved["model"])
                    if restored.training_fingerprint == fingerprint and restored.patterns_enabled == enabled:
                        predictor = restored
                except (KeyError, TypeError, ValueError, OverflowError):
                    logger.warning("Ignoring an invalid Ghosty model for guild %s", guild_id)
            if predictor is None:
                rows = [
                    {**row, "created_at": datetime.fromisoformat(row["created_at"])}
                    for row in examples
                ]
                predictor = GhostyPredictor(rows, now=now, enabled=enabled)
                predictor.training_fingerprint = fingerprint
                logger.info(
                    "Trained Ghosty predictor: guild=%s samples=%s status=%s",
                    guild_id, predictor.samples, predictor.validation["status"],
                )
        try:
            self._write(directory / "training.json", {
                "version": 1, "guild_id": str(guild_id),
                "dataset_sha256": fingerprint, "samples": len(examples), "examples": examples,
            })
            self._write(model_path, {"guild_id": str(guild_id), "model": predictor.to_state()})
        except OSError:
            # MongoDB remains authoritative. Prediction and recording can
            # continue, and the next background check retries the disk save.
            logger.exception("Could not persist Ghosty training files for guild %s", guild_id)
        return predictor
