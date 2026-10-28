from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


DEFAULT_MINIMUM_SAMPLES = 50


DASHBOARD_REFRESH_SECONDS = 60


DASHBOARD_HEADING = "## @ t r a c k e r"


DASHBOARD_MESSAGE_KEYS = (
    "dashboard_tracker_message_id",
    "dashboard_message_id",
    "dashboard_controls_message_id",
)


BACKFILL_IMPORT_CONFIRMATION = "IMPORT"


TEMPCHANNEL_SETTINGS_COLOR = 0x7CC6B0


TRACKER_COLOR = 0xF5276F


HIT_SUMMARY_COLOR = 0x7CC6B0


TEMPCHANNEL_SETTINGS_THUMBNAIL = (
    Path(__file__).resolve().parents[3] / "assets" / "tempchannel-settings.webp"
)
