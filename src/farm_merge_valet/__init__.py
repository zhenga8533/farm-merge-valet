"""Farm Merge Valet: CDP and internal-API automation for Farm Merge Valley."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("farm-merge-valet")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
