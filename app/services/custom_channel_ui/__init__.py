"""Custom channel ui public interface."""

from app.database.repositories.channel_repository import set_channel_config

from .constants import CONTENT_TYPES as CONTENT_TYPES
from .constants import DEFAULT_GIF_DOMAINS as DEFAULT_GIF_DOMAINS
from .constants import DEFAULT_RULES as DEFAULT_RULES
from .content import ContentPage as ContentPage
from .gif_domains import GifDomainsPage as GifDomainsPage
from .modals import GIFDomainsModal as GIFDomainsModal
from .view import CustomChannelView as CustomChannelView
from .whitelist import WhitelistPage as WhitelistPage

__all__ = [
    "CONTENT_TYPES",
    "ContentPage",
    "CustomChannelView",
    "DEFAULT_GIF_DOMAINS",
    "DEFAULT_RULES",
    "GIFDomainsModal",
    "GifDomainsPage",
    "WhitelistPage",
    "set_channel_config",
]
