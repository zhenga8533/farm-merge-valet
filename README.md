# Farm Merge Valet

A screen-automation tool for [Farm Merge Valley](https://discord.com/discovery/applications/1187013846746005515),
a merge/farming game embedded as an app inside host clients like Discord
(and elsewhere). This project does **not** modify or reimplement the game —
it observes the screen and drives mouse/keyboard input to automate play.

Because the game can run inside different host applications (Discord
desktop client, a browser tab, etc.) rather than as its own standalone
process, automation is host-agnostic: it targets a window by title and
operates purely on pixels, so it works regardless of which app is hosting
the game.

## Status

Early scaffolding. The CLI can locate the target window and run an empty
perceive-decide-act loop; game-specific vision templates and strategy are
not yet implemented.

## Architecture

```
src/farm_merge_valet/
  capture/        Locate the host window and grab screenshots of it (mss)
  vision/         Template matching to find UI elements in a frame (OpenCV)
  actions/        Mouse/keyboard simulation, scoped to the target window
  core/           Bot loop (perceive -> decide -> act) and run statistics
  integrations/   Outbound integrations (e.g. Discord webhook stat posts)
  cli.py          Typer-based CLI entry point
```

Planned, not yet built:
- Game-specific vision templates + decision logic in `core/`
- A GUI (separate from the CLI) for monitoring/controlling the bot
- Additional integrations beyond Discord webhooks

## Setup

Requires Python 3.11+ on Windows.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Edit `.env` to set `FMV_WINDOW_TITLE` to a substring of your target
window's title (run `farm-merge-valet list-windows` to see open titles).

## Usage

```powershell
farm-merge-valet list-windows   # see open window titles
farm-merge-valet calibrate      # verify the target window can be found
farm-merge-valet run            # start the automation loop
```

## Development

```powershell
pytest
ruff check .
mypy src
```

## Configuration

All settings are environment variables prefixed `FMV_` (see
`.env.example`), loaded via `pydantic-settings`. Key options:

| Variable                 | Purpose                                             |
| ------------------------ | ---------------------------------------------------- |
| `FMV_WINDOW_TITLE`       | Substring to match the host window's title           |
| `FMV_MATCH_CONFIDENCE`   | Minimum template-match confidence (0-1)               |
| `FMV_LOOP_INTERVAL`      | Seconds between bot loop iterations                   |
| `FMV_DISCORD_WEBHOOK_URL`| Webhook URL for posting run stats to Discord (optional)|
| `FMV_LOG_LEVEL`          | Logging verbosity                                     |
