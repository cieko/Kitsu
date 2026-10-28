from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class StealItem:
    item_type: Literal["emoji", "sticker"]
    name: str
    item_id: int
    url: str
    animated: bool
    source_format: str

    @property
    def export_options(self) -> list[str]:
        if self.source_format == "lottie":
            return ["JSON"]

        if self.animated:
            return ["GIF", "APNG"]

        if self.source_format == "unknown":
            return ["PNG"]

        return ["PNG", "GIF", "WEBP", "JPEG"]
