"""Ghosty public interface."""

import asyncio

from app.database.repositories import ghosty_pause_repository as pause_repository
from app.database.repositories import ghosty_repository as repository
from app.database.repositories import (
    ghosty_tempchannel_repository as tempchannel_repository,
)
from app.services.emoji_services import get_application_emoji
from app.services.ghosty_pause_ui import PauseSetupView as PauseSetupView
from app.services.ghosty_reaction_service import get_hit_reaction
from app.services.ghosty_service import GHOSTY_BOT_ID
from app.services.ghosty_tempchannel_service import DURATION_PRESETS
from app.services.ghosty_tempchannel_service import TempChannelError as TempChannelError

from .backfill import BackfillCommands as BackfillCommands
from .cog import GhostyCog as GhostyCog
from .cog import setup as setup
from .constants import BACKFILL_IMPORT_CONFIRMATION as BACKFILL_IMPORT_CONFIRMATION
from .constants import DASHBOARD_HEADING as DASHBOARD_HEADING
from .constants import DASHBOARD_MESSAGE_KEYS as DASHBOARD_MESSAGE_KEYS
from .constants import DASHBOARD_REFRESH_SECONDS as DASHBOARD_REFRESH_SECONDS
from .constants import DEFAULT_MINIMUM_SAMPLES as DEFAULT_MINIMUM_SAMPLES
from .constants import HIT_SUMMARY_COLOR as HIT_SUMMARY_COLOR
from .constants import TEMPCHANNEL_SETTINGS_COLOR as TEMPCHANNEL_SETTINGS_COLOR
from .constants import TEMPCHANNEL_SETTINGS_THUMBNAIL as TEMPCHANNEL_SETTINGS_THUMBNAIL
from .constants import TRACKER_COLOR as TRACKER_COLOR
from .constants import logger as logger
from .controls import DashboardControls as DashboardControls
from .dashboard import GhostyDashboard as GhostyDashboard
from .dashboard_delivery import DashboardDelivery as DashboardDelivery
from .groups import ghosty as ghosty
from .groups import tempchannel as tempchannel
from .groups import tempchannels as tempchannels
from .guards import GhostyGuards as GhostyGuards
from .live import GhostyLiveResults as GhostyLiveResults
from .market_settings import show_settings as show_settings
from .market_settings_page import build_settings_page as build_settings_page
from .member_selects import AddMemberSelect as AddMemberSelect
from .member_selects import RemoveMemberSelect as RemoveMemberSelect
from .owner_commands import OwnerCommands as OwnerCommands
from .policies import show_policy as show_policy
from .policies import show_terms as show_terms
from .prefix_commands import PrefixCommands as PrefixCommands
from .quota_commands import QuotaCommands as QuotaCommands
from .settings_menu import show_settings_menu as show_settings_menu
from .staff_commands import StaffCommands as StaffCommands
from .tempchannel_settings import (
    GhostyTempChannelSettingsView as GhostyTempChannelSettingsView,
)
from .tracking_commands import TrackingCommands as TrackingCommands

__all__ = [
    "AddMemberSelect",
    "BACKFILL_IMPORT_CONFIRMATION",
    "BackfillCommands",
    "DASHBOARD_HEADING",
    "DASHBOARD_MESSAGE_KEYS",
    "DASHBOARD_REFRESH_SECONDS",
    "DEFAULT_MINIMUM_SAMPLES",
    "DURATION_PRESETS",
    "DashboardControls",
    "DashboardDelivery",
    "GHOSTY_BOT_ID",
    "GhostyCog",
    "GhostyDashboard",
    "GhostyGuards",
    "GhostyLiveResults",
    "GhostyTempChannelSettingsView",
    "HIT_SUMMARY_COLOR",
    "OwnerCommands",
    "PrefixCommands",
    "QuotaCommands",
    "RemoveMemberSelect",
    "StaffCommands",
    "TEMPCHANNEL_SETTINGS_COLOR",
    "TEMPCHANNEL_SETTINGS_THUMBNAIL",
    "TRACKER_COLOR",
    "TrackingCommands",
    "asyncio",
    "build_settings_page",
    "get_application_emoji",
    "get_hit_reaction",
    "ghosty",
    "logger",
    "pause_repository",
    "repository",
    "setup",
    "show_policy",
    "show_settings",
    "show_settings_menu",
    "show_terms",
    "tempchannel",
    "tempchannel_repository",
    "tempchannels",
]

__all__ += ["PauseSetupView", "TempChannelError"]
