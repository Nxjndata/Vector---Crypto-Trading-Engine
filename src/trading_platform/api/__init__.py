"""API package for the Trading Platform."""

from trading_platform.api.app import create_app
from trading_platform.api.container import PlatformContainer, get_container, set_container

__all__ = ["create_app", "PlatformContainer", "get_container", "set_container"]
