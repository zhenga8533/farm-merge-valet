"""Farm Merge Valet: CDP and internal-API automation for Farm Merge Valley."""

import tomllib
from importlib import metadata
from pathlib import Path

_DISTRIBUTION = "farm-merge-valet"


def _checkout_version(package_dir: Path) -> str | None:
    """Read the version from pyproject.toml when running from a source checkout.

    An editable install records its version once, at install time, and keeps
    reporting it after later version bumps, so the checkout's own metadata wins.
    """
    if package_dir.parent.name != "src":
        return None
    try:
        pyproject = (package_dir.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        project = tomllib.loads(pyproject)["project"]
    except (OSError, ValueError, KeyError):
        return None
    if project.get("name") != _DISTRIBUTION:
        return None
    version = project.get("version")
    return version if isinstance(version, str) else None


def _resolve_version(package_dir: Path) -> str:
    checkout_version = _checkout_version(package_dir)
    if checkout_version is not None:
        return checkout_version
    try:
        return metadata.version(_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version(Path(__file__).resolve().parent)
