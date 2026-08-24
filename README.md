# Farm Merge Valet

Farm Merge Valet automates Farm Merge Valley through the Chromium DevTools
Protocol (CDP) and the game's own interaction systems. It reads authoritative board and
inventory state, preserves the merge-5 planner and board-space policy, and
submits item drops and supply claims without moving the mouse or typing into the
game.

The current loop claims supply crates, plans merge-3/merge-5 actions (including
swaps), and verifies every submitted action against the next authoritative board
state. Order fulfillment, product collection, obstacle clearing, and visits are
not implemented yet.

## Setup

Python 3.11+ and a Chromium-family browser are required. Google Chrome and
Microsoft Edge are supported. Brave and Chromium are experimental targets.
Firefox is not supported because this implementation requires Chromium CDP.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

The project creates and launches a dedicated browser profile with its required
debugging and background-operation switches:

```powershell
farm-merge-valet browser list
farm-merge-valet browser launch
farm-merge-valet browser status
```

`auto` prefers Chrome and then Edge. Pass `--browser edge`, `--browser brave`,
or `--browser chromium` to override it. `browser stop` and `browser restart`
refuse to close a browser unless its executable, profile, debugging port, and
Farm Merge Valet ownership marker all match. Other browser profiles are not
targeted. The dedicated profile persists cookies and the Reddit login across
managed-browser restarts; changing its directory or browser creates a separate
session.

Open the Reddit post and click **Play**. The managed browser must remain open and
the game loaded; minimized and headless operation are outside the supported
scope. The window may be unfocused, on another virtual desktop, or showing a
different tab. The bot does not change fullscreen state, zoom, the cursor, or
the foreground application, and it does not intentionally pan the game camera.

## Usage

```powershell
farm-merge-valet list-targets
farm-merge-valet capture
farm-merge-valet visualize-positions
farm-merge-valet diagnose-live-state
farm-merge-valet run
```

`run` reuses a healthy browser on the configured port or launches the managed
profile when the endpoint is absent. It refuses an occupied endpoint with the
wrong profile, executable, or switches. The bot starts paused by default. `F9`
pauses/resumes and `F10` quits; Ctrl+C is also supported. These are inbound
controls only and never drive game actions.
An ordinary resume validates and reuses the cached scene; heap discovery runs
again only when the iframe, gameplay services, or active board identity changed.
Single-key hotkeys fire once on key-down and latch until release; quit is
idempotent across the keyboard and GUI shutdown paths.

`capture` uses `Page.captureScreenshot` and live DOM geometry to save only the
game iframe without focusing the browser. `visualize-positions` marks visible cells
by current classification. `diagnose-live-state` reports board-map candidates,
runtime capabilities, scene identity, animation-heartbeat state, and whether
the browser has the required background flags. The potentially expensive
heap-wide candidate list is omitted unless `--include-heap-candidates` is used.

## How actions work

Each iteration follows a short, fail-closed cycle:

1. Perceive authoritative board, inventory, runtime, and heartbeat state.
2. Plan with the existing merge and reserve policies.
3. Submit through the game's pick/drag/drop or crate-spawn handler.
4. Verify against authoritative board state before another action is submitted.

A `requestAnimationFrame` heartbeat must advance before actions are sent. If it
stalls, the bot observes without queueing or retrying. A submitted item action
remains pending until active, settled board state confirms success, a different
change, or a genuine no-op. Genuine no-ops cool down before retry, and three
failures of the same action pause the bot. Runtime incompatibility reports an
unavailable capability; there is no mouse-input fallback.

Internet or server interruptions are not detected separately yet. The bot keeps
using passive reconnect behavior whenever the local game loop and live state
remain available.

CDP connections are persistent per browser target, bypass proxy discovery for
the local DevTools socket, and apply bounded command deadlines. Pause and quit
cancel in-flight reads; a timeout closes the stale socket, refreshes the target,
and retries once. Slow reads and stable waiting reasons are logged instead of
leaving the bot apparently silent. If any required background flag is missing,
startup pauses without taking actions.

## Configuration

See [`.env.example`](.env.example). Browser settings include `FMV_BROWSER`,
`FMV_BROWSER_EXECUTABLE`, `FMV_BROWSER_PROFILE_DIR`, `FMV_BROWSER_AUTO_LAUNCH`,
and `FMV_GAME_URL`. Other important settings are `FMV_CDP_PORT`,
`FMV_WINDOW_TITLE` (a Reddit page-title or URL substring),
`FMV_PREFER_MERGE_FIVE`, `FMV_MERGE_EMPTY_CELL_RESERVE`, and the item-action
and crate-delay ranges. Pause/quit hotkeys, GUI options, and log
level are also configurable. Physical-input timing, viewport, dead-zone, pan,
and template-confidence settings no longer exist.

## Logging

The default `INFO` level reports controls, runtime readiness, confirmed actions,
crate claims, and rate-limited waiting conditions. `WARNING` identifies
recoverable failures or unusually slow operations; `ERROR` identifies a
condition that pauses or prevents safe operation. Set `FMV_LOG_LEVEL=DEBUG` for
target refreshes, cached discovery, and planner phase transitions.

## Development

```powershell
pytest --cov=farm_merge_valet
ruff check .
ruff format --check .
mypy src
```

See [Automation Methodology](docs/automation-methodology.md) and
[Game Mechanics](docs/game-mechanics.md) for additional context.
