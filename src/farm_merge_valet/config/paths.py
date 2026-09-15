"""Platform-specific application data and cache locations."""

import os
import sys
from pathlib import Path


def user_data_root() -> Path:
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / "FarmMergeValet"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FarmMergeValet"
    if xdg_data_home := os.environ.get("XDG_DATA_HOME"):
        return Path(xdg_data_home) / "farm-merge-valet"
    return Path.home() / ".local" / "share" / "farm-merge-valet"


def user_config_path() -> Path:
    return user_data_root() / "config.json"


def user_cache_root() -> Path:
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / "FarmMergeValet" / "Cache"
    if xdg_cache_home := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg_cache_home) / "farm-merge-valet"
    return Path.home() / ".cache" / "farm-merge-valet"
