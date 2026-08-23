# Farm Merge Valet

Farm Merge Valet automates parts of Farm Merge Valley by reading the live game
board through Chrome DevTools Protocol (CDP) and driving ordinary mouse input.
It does not modify or reimplement the game.

## Status

Early, usable automation for the crate-and-merge loop:

- finds and activates the configured Chrome window;
- normalizes the game zoom and camera position;
- reads board contents and render geometry from the live game iframe via CDP;
- claims supply crates while preserving space for merge rearrangements;
- plans and performs merge-3 or merge-5 actions, including regrouping items;
- exposes global pause/quit hotkeys and an optional color-coded log overlay.

Order fulfillment, product collection, obstacle clearing, visits, and other
gameplay phases are not implemented yet. See
[Automation Methodology](docs/automation-methodology.md) for the current loop
and roadmap.

Version 0.1 intentionally targets the Reddit-hosted game in Chrome with remote
debugging. Supporting other hosts is deferred until this path is stable.

## Architecture

```text
src/farm_merge_valet/
  cdp/             Read live board state and board-to-screen geometry
  capture/         Locate the target window and capture screenshots
  vision/          Match fixed UI templates across plausible scales
  actions/         Send mouse input relative to the target window
  core/            Model the board, plan merges, and run the bot loop
  gui/             Optional live-log overlay
  tools/           Extract templates from game sprite atlases
  cli.py           Typer-based CLI entry point
```

## Setup

Requires Python 3.11+ on Windows and a separate Chrome profile for remote
debugging.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Launch Chrome with remote debugging enabled. Chrome requires a non-default
profile for this mode:

```powershell
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\path\to\fmv-profile"
```

Open the Reddit post containing the game, click **Play**, then set
`FMV_WINDOW_TITLE` in `.env` to a distinctive substring of that Chrome
window's title. `farm-merge-valet list-windows` shows the available titles.

## Usage

```powershell
farm-merge-valet list-windows
farm-merge-valet calibrate
farm-merge-valet capture
farm-merge-valet visualize-positions
farm-merge-valet run
```

`run` starts paused by default. Press the configured pause hotkey (`F9` by
default) to enter Chrome F11 mode, expand Reddit's game view, normalize the
camera, and begin. Press it again to pause. The default quit hotkey is `F10`.
Hotkey callbacks only signal the bot, so they are not blocked by CDP reads or
environment setup. Pause and quit cancel pending waits and stop before the next
input; a mouse gesture already underway may take up to 0.3 seconds to finish.

Template assets can be refreshed from a browser network capture:

```powershell
farm-merge-valet extract-templates path\to\game.har
```

To inspect live board-map candidates and the complete board-to-screen
coordinate transform without taking gameplay actions:

```powershell
farm-merge-valet diagnose-live-state
```

The command saves a JSON report under `captures/`.

## Configuration

Settings are loaded from `.env` using the `FMV_` prefix. See
[`.env.example`](.env.example) for the complete, annotated list. The main
settings are:

| Variable | Purpose |
| --- | --- |
| `FMV_WINDOW_TITLE` | Distinctive substring of the target Chrome window title |
| `FMV_CDP_PORT` | Chrome remote-debugging port |
| `FMV_PREFER_MERGE_FIVE` | Prefer efficient merge-5 actions instead of merge-3 |
| `FMV_MERGE_EMPTY_CELL_RESERVE` | Empty cells preserved for merge rearrangements |
| `FMV_BOARD_DEAD_*_RATIO` | Proportional outer UI slices excluded from actions |
| `FMV_CRATE_DEAD_*_RATIO` | Bottom-center crate-button exclusion rectangle |
| `FMV_PAN_ANCHOR_*_RATIO` | Safe background point used to drag the board camera |
| `FMV_PAUSE_HOTKEY` / `FMV_QUIT_HOTKEY` | Global bot controls |
| `FMV_START_PAUSED` | Wait for an explicit resume before touching the game |
| `FMV_GUI_ENABLED` | Show the optional live-log overlay |
| `FMV_LOG_LEVEL` | Logging verbosity |

## Development

```powershell
pytest --cov=farm_merge_valet
ruff check .
mypy src
```
