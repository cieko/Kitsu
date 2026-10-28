from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


MOODS = (
    "happy", "fun", "sad", "angry", "love", "surprised", "confused",
    "scared", "sarcastic", "bully", "smug", "sleepy", "celebration",
    "neutral", "unknown",
)
NSFW_LEVELS = ("likely", "unlikely", "uncertain")
NUDITY_LEVELS = ("likely", "unlikely", "uncertain")
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "moods": {"type": "array", "items": {"type": "string", "enum": list(MOODS)},
                  "minItems": 1, "maxItems": 3},
        "nsfw": {"type": "string", "enum": list(NSFW_LEVELS)},
        "nudity": {"type": "string", "enum": list(NUDITY_LEVELS)},
        "reason": {"type": "string", "minLength": 1, "maxLength": 240},
    },
    "required": ["moods", "nsfw", "nudity", "reason"],
    "additionalProperties": False,
}


class MoodError(Exception):
    """An expected classification failure with a user-readable message."""


@dataclass(frozen=True, slots=True)
class MoodAsset:
    kind: str
    name: str
    item_id: int = 0
    url: str = ""
    source_format: str = ""
    unicode: str = ""

    @property
    def key(self) -> str:
        if self.kind == "unicode":
            return f"unicode:{self.unicode}"
        return f"{self.kind}:{self.item_id}:{self.source_format}:{self.name_key}"

    @property
    def name_key(self) -> str:
        return hashlib.sha256(self.name.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MoodResult:
    moods: tuple[str, ...]
    nsfw: str
    reason: str
    sampled_frames: int = 0
    total_frames: int = 0
    nudity: str = "uncertain"

    @classmethod
    def parse(cls, text: str, *, sampled_frames=0, total_frames=0) -> MoodResult:
        try:
            value = json.loads(text)
            if not isinstance(value, dict) or set(value) != {"moods", "nsfw", "nudity", "reason"}:
                raise ValueError
            moods, nsfw, reason = value["moods"], value["nsfw"], value["reason"]
            nudity = value["nudity"]
            if not isinstance(moods, list) or not 1 <= len(moods) <= 3:
                raise ValueError
            if any(not isinstance(mood, str) or mood not in MOODS for mood in moods):
                raise ValueError
            if nsfw not in NSFW_LEVELS or nudity not in NUDITY_LEVELS or not isinstance(reason, str):
                raise ValueError
            reason = " ".join(reason.split())
            if not reason or len(reason) > 240:
                raise ValueError
            moods = tuple(dict.fromkeys(moods))
            specific = tuple(mood for mood in moods if mood not in {"neutral", "unknown"})
            moods = specific or (("unknown",) if "unknown" in moods else ("neutral",))
            return cls(moods, nsfw, reason, sampled_frames, total_frames, nudity=nudity)
        except (ValueError, KeyError, TypeError) as error:
            raise MoodError("I couldn't confidently classify that emoji or sticker.") from error
