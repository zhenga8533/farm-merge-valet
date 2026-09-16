# Changelog

## 0.1.1 - 2026-09-15

- Changed force lucky merge to retry a failed save/reload confirmation with a
  settle check instead of pausing the entire bot; failures now cool down and
  retry only that item instead of halting automation.
- Fixed the Statistics page freezing while visible: periodic refreshes now
  skip unchanged snapshots and update table cells in place instead of
  rebuilding the whole table and chart every few seconds.
- Fixed a data-corruption bug where sorting the Statistics breakdown table by
  a column other than the default could apply a refreshed value to the wrong
  row.
- Changed the activity trend chart to stack activity and reliability bars,
  added a color-swatch legend, gridlines, a hover highlight, and multiple
  x-axis date labels instead of only the range's start and end.
- Changed the Statistics page's "Warnings / errors" tile to "Reliability
  events" so it matches the trend chart's own definition (recoveries,
  board-blocks, and action failures, in addition to warnings and errors).
- Changed the Dashboard and Statistics pages to cap their content width,
  matching the Settings pages, instead of stretching cards on wide windows.
- Documented the standalone Windows executable as the primary install path
  in the README, alongside the existing source/uv instructions.

## 0.1.0 - 2026-09-15

- Added authoritative board automation for interactions, merging, producers,
  shops, crates, marketplace offers, obstacles, land expansion, and repairs.
- Added optional farm visits, event-island automation, event reward claiming,
  visitor interaction claims, and force-lucky merges.
- Added a desktop policy editor, browser lifecycle controls, statistics,
  diagnostics, structured logging, and Discord summaries.
- Added consistent application, tray, taskbar, and Windows executable branding.
- Added optional startup notifications for newer public GitHub releases.
- Added explicit recovery for the game's disconnection layer.
- Added platform-aware catalog synchronization and generated asset caching.
