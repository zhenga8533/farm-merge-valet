from __future__ import annotations

import pytest

from farm_merge_valet.gui.services.update_checker import AvailableUpdate, available_update


def _release(tag: str, url: str | None = None) -> dict[str, str]:
    return {
        "tag_name": tag,
        "html_url": url or f"https://github.com/zhenga8533/farm-merge-valet/releases/tag/{tag}",
    }


def test_newer_release_is_available() -> None:
    assert available_update(_release("v0.2.0"), "0.1.0") == AvailableUpdate(
        "0.2.0",
        "https://github.com/zhenga8533/farm-merge-valet/releases/tag/v0.2.0",
    )


@pytest.mark.parametrize("tag", ["v0.1.0", "v0.0.9"])
def test_current_or_older_release_is_ignored(tag: str) -> None:
    assert available_update(_release(tag), "0.1.0") is None


def test_untrusted_release_url_is_ignored() -> None:
    assert available_update(_release("v0.2.0", "https://example.test/release"), "0.1.0") is None


def test_malformed_release_response_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing its tag or URL"):
        available_update({}, "0.1.0")
