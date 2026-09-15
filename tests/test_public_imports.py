def test_supported_package_exports_resolve() -> None:
    from farm_merge_valet import config
    from farm_merge_valet.automation import Bot, Phase, RuntimeConnectionError
    from farm_merge_valet.browser import BrowserManager
    from farm_merge_valet.gui import run_application
    from farm_merge_valet.observability.discord import DiscordWebhookHandler
    from farm_merge_valet.observability.logging import configure_logging

    assert config.AppConfig
    assert Bot
    assert Phase
    assert BrowserManager
    assert RuntimeConnectionError
    assert run_application
    assert configure_logging
    assert DiscordWebhookHandler
