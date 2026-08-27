from __future__ import annotations

import json
import zipfile

from farm_merge_valet.browser import BrowserStatus
from farm_merge_valet.config import AppConfig
from farm_merge_valet.diagnostics import export_support_bundle


def test_support_bundle_is_sanitized_and_survives_offline_runtime(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        browser_executable=tmp_path / "private" / "browser.exe",
        browser_profile_dir=tmp_path / "private" / "profile",
        discord_webhook_url="https://example.test/private-webhook",
        catalog_dir=tmp_path / "catalog",
        atlas_cache_dir=tmp_path / "atlases",
    )

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def status(self) -> BrowserStatus:
            return BrowserStatus(False, False, False, detail="offline")

        @staticmethod
        def status_dict(status: BrowserStatus) -> dict[str, object]:
            return {"running": status.running, "detail": status.detail}

    monkeypatch.setattr("farm_merge_valet.diagnostics.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr(
        "farm_merge_valet.diagnostics.collect_live_state_report",
        lambda _config: (_ for _ in ()).throw(RuntimeError("game unavailable")),
    )

    output = export_support_bundle(config, tmp_path / "support", logs="hello")

    with zipfile.ZipFile(output) as archive:
        report = json.loads(archive.read("diagnostics.json"))
        assert archive.read("application.log") == b"hello"
    serialized = json.dumps(report)
    assert "private-webhook" not in serialized
    assert str(tmp_path / "private") not in serialized
    assert report["live_state_error"] == "game unavailable"
