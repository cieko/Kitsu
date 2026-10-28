"""Instant, outcome-specific Ghosty reactions from a cached local message bank."""

from __future__ import annotations

from functools import lru_cache
import json
import logging
from pathlib import Path
import random


logger = logging.getLogger(__name__)
REACTIONS_FILE = Path(__file__).resolve().parents[2] / "assets" / "ghosty_reactions.json"
FALLBACK_REACTIONS = {
    "win": ("A net win! Ghosty says you're welcome. 🎉",),
    "loss": ("Ghosty says you're welcome; the remaining loss would like a word. 🙃",),
    "break_even": ("All that suspense just to break even—classic Ghosty. 😅",),
}


@lru_cache(maxsize=1)
def load_hit_reactions() -> dict[str, tuple[str, ...]]:
    """Read once; an unavailable or invalid file must not block hit delivery."""
    try:
        with REACTIONS_FILE.open(encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, dict):
            raise ValueError("Reaction file must contain an object")
        reactions = {}
        for outcome in FALLBACK_REACTIONS:
            messages = data.get(outcome)
            if not isinstance(messages, list) or not messages or not all(
                isinstance(message, str) and message.strip() for message in messages
            ):
                raise ValueError(f"Missing or invalid {outcome} reactions")
            reactions[outcome] = tuple(message.strip() for message in messages)
        return reactions
    except (OSError, ValueError):
        logger.exception("Could not load Ghosty reaction file; using local defaults.")
        return FALLBACK_REACTIONS


def get_hit_reaction(*, net_result: int) -> str:
    outcome = "win" if net_result > 0 else "loss" if net_result < 0 else "break_even"
    return random.choice(load_hit_reactions()[outcome])
