"""Emoji appearance classification through an existing Ollama vision model."""

from .models import MoodAsset, MoodError, MoodResult
from .service import MoodClassifier

__all__ = ["MoodAsset", "MoodClassifier", "MoodError", "MoodResult"]
