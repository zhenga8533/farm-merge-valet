"""Application configuration.

Settings are loaded from environment variables / a `.env` file, with sane
defaults for local development. See `.env.example` for available options.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FMV_",
        extra="ignore",
    )

    # Window targeting: substring match against open window titles.
    window_title: str = "Discord"

    # Port Chrome's remote debugging endpoint is expected on -- see
    # cdp/client.py for why the bot needs this rather than just working
    # against a normal, already-open browser window.
    cdp_port: int = 9222

    # Where template images used for vision matching live.
    templates_dir: Path = PROJECT_ROOT / "assets" / "templates"

    # Where downloaded game atlas PNGs/manifests are cached between
    # `extract-templates` runs, so re-running without --force doesn't
    # re-fetch everything.
    atlas_cache_dir: Path = PROJECT_ROOT / ".atlas_cache"

    # Minimum confidence (0-1) for template matches to be accepted.
    match_confidence: float = 0.85

    # The "Need more empty space!" banner (core/bot.py's board-full check)
    # gets its own, more lenient threshold: confirmed live, a real,
    # unmistakably-on-screen instance of it only scored 0.838 against
    # `error_need_space.png` -- just under `match_confidence` -- while an
    # unrelated frame with no banner at all scored 0.75 at its best
    # (coincidental) match elsewhere. 0.8 sits safely between the two.
    # Missing this check has real cost (the bot keeps hammering supply
    # crate clicks with nowhere for them to go -- confirmed live), so
    # erring toward catching a real banner matters more here than for
    # item/background matching generally. Only actually used as a
    # fallback now -- see `crate_click_batch_size` below -- since the
    # banner turned out unreliable (confirmed live: it didn't appear even
    # once across dozens of crate clicks against a genuinely full board)
    # once a much better signal was available: `BoardGrid.find_empty()`
    # on the live-synced board (see `cdp/board_store.py`) directly
    # answers "is there anywhere left to spawn an item" from the game's
    # own data, no vision involved.
    need_space_confidence: float = 0.8

    # How many supply-crate clicks `Bot._step_claim_crates` fires off
    # before re-syncing the live board state to check whether it's full.
    # Each click is already a real action with its own settle delay: the
    # live-state check itself is cheap (a couple of CDP round trips, not
    # a vision scan), so this exists purely to spend fewer of those round
    # trips during a long burst of genuinely productive clicks, not
    # because checking every time is expensive.
    crate_click_batch_size: int = 10

    # Delay between crate clicks, just long enough for the game to
    # register one click before the next -- much shorter than
    # `loop_interval`, since a click doesn't need board scanning to
    # justify a full second (see `Bot._step_claim_crates`).
    crate_click_settle: float = 0.1

    # Fixed UI chrome bounding box, in captured-frame pixels: above
    # `board_min_pixel_y` is the browser tabs/address bar; left of
    # `board_min_pixel_x`/right of `board_max_pixel_x` are the level
    # badge/quest-list column and the settings/currency icon column.
    # Used to decide whether a planned merge drag's endpoints actually
    # land somewhere real/clickable (`Bot._is_action_visible`) rather than
    # on UI chrome, since board content itself comes from the game's live
    # state and can reference any cell on the whole map, not just what's
    # currently on screen. There's no real board-boundary detector yet;
    # these are pragmatic stand-ins tuned to this specific window layout.
    board_min_pixel_x: float = 200.0
    board_max_pixel_x: float = 1700.0
    board_min_pixel_y: float = 170.0

    # Default merge-5 preference (merging exactly 5 identical items yields
    # 2 of the next tier instead of 1 from a merge-3 -- see
    # docs/game-mechanics.md). Per-item/tier overrides aren't env-var
    # configurable yet; pass `merge_five_overrides` to `Bot` directly.
    prefer_merge_five: bool = False

    # Seconds between bot loop iterations.
    loop_interval: float = 1.0

    # Global hotkeys (work even when the terminal isn't focused, since the
    # bot is busy driving mouse input elsewhere) to pause/resume and quit
    # `run_forever`. See `keyboard` package syntax for valid values, e.g.
    # "ctrl+alt+p".
    pause_hotkey: str = "f9"
    quit_hotkey: str = "f10"

    # Start paused rather than acting immediately -- lets you get the
    # target window in position (and check the overlay GUI/logs, if
    # enabled) before anything drives mouse input. `initialize()` (the
    # zoom-out/scroll-to-bottom environment setup) is deferred until the
    # first resume too, same as pausing mid-run already does, so nothing
    # touches the game at all until you press `pause_hotkey`.
    start_paused: bool = True

    # Optional Discord webhook URL for posting run statistics.
    discord_webhook_url: str | None = None

    # Logging verbosity: DEBUG, INFO, WARNING, ERROR.
    log_level: str = "INFO"

    # Overlay GUI (see gui/overlay.py) -- off by default so `run` keeps its
    # existing terminal-only behavior unless explicitly opted into.
    gui_enabled: bool = False
    # 0 (fully transparent) - 1 (fully opaque).
    gui_opacity: float = 0.85
    # Always-on-top, and click-through (clicks land on whatever's behind
    # it) *while it isn't the active window* -- alt-tab or click its
    # taskbar entry to interact with it normally (drag, resize, scroll
    # the log) until it loses focus again. Disable for a plain window
    # instead -- see gui/overlay.py.
    gui_overlay_mode: bool = True


settings = Settings()
