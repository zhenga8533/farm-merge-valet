"""Application pages grouped by user-facing responsibility."""

from farm_merge_valet.gui.pages.browser import BrowserPage
from farm_merge_valet.gui.pages.buildings import BuildingsPage
from farm_merge_valet.gui.pages.dashboard import DashboardPage
from farm_merge_valet.gui.pages.items import ItemsPage
from farm_merge_valet.gui.pages.logs import LogsPage
from farm_merge_valet.gui.pages.marketplace import MarketplacePage
from farm_merge_valet.gui.pages.settings import SettingsPage
from farm_merge_valet.gui.pages.shops import ShopsPage

__all__ = [
    "BrowserPage",
    "BuildingsPage",
    "DashboardPage",
    "ItemsPage",
    "LogsPage",
    "MarketplacePage",
    "SettingsPage",
    "ShopsPage",
]
