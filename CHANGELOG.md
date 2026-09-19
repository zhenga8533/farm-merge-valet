# Changelog

## 0.2.2 - 2026-09-19

- Fixed automatic recovery giving up after a transient DevTools disconnect
  (for example, right after the OS resumes from sleep) instead of retrying
  like other connection losses.

## 0.2.1 - 2026-09-18

- Fixed building repair targeting to exclude buildings already mid-repair, so
  an in-progress repair's already-consumed materials no longer keep getting
  reserved and prioritized as still needed for its entire animation.

## 0.2.0 - 2026-09-18

- Added a configurable obstacle priority, including a southernmost-position
  tiebreaker, and kept focus on partially cleared obstacles when a board refresh
  changes their object ID.
- Fixed coin-cost land expansion detection by using the standard area's own
  unlockable state instead of the premium area's state.
- Added automatic recovery when the board remains unavailable mid-session after
  the runtime has had a full recovery cycle. Recovery now reopens the game page
  when its iframe disappears and keeps retrying while the page remains unavailable.
- Added group-level repair toggles for Buildings and shared policy controls
  across catalog pages. Buildings and Marketplace now update existing controls
  without rebuilding their trees on routine configuration changes.
- Improved desktop controls with a Discord webhook reveal button, consistent
  spin box buttons, and pages that use the available window width.
- Enabled interaction by default for all upgrade card tiers and corrected
  pause and recent-activity status updates.
- Removed redundant code, comments, and unused test fixtures.

## 0.1.4 - 2026-09-16

- Changed land expansion to target the cheapest unlockable standard-cost area
  and, when only premium areas are eligible, the one furthest south, instead
  of the game's single suggested "next" area. Several areas can be
  unlockable at once, and the game's own ordering didn't favor either cost
  or position.
- Fixed game-page recovery giving up immediately if the managed browser tab
  was momentarily unreachable (for example, a transient wifi drop) right as
  recovery started; it now retries with backoff before giving up.
- Added a configurable limit on how many times game-page recovery is
  attempted before automation stops (`max_game_recovery_attempts`, 0 for
  unlimited), with the attempt count resetting after an hour of healthy
  running so an old failure streak doesn't count against a later, unrelated
  one.
- Fixed the Statistics breakdown table clipping "Breakdown" column text
  mid-line instead of eliding it, and its sorted column header label
  sometimes losing its own text (for example, showing "Met..." for
  "Metric") to a mis-sized column.
- Fixed the Statistics breakdown table's duration values displaying as a raw
  seconds count (for example, "43,200") instead of readable time
  (for example, "12h 0m").

## 0.1.3 - 2026-09-16

- Fixed the bot waiting indefinitely, with no recovery, when the game's
  iframe could no longer be paired with a supported portal page mid-session
  (for example, a page overlay interfering with the managed tab). It now
  requests the existing automatic recovery (reloading the managed game page)
  after the condition persists for 60 seconds.

## 0.1.2 - 2026-09-16

- Fixed obstacle-clearing automation abandoning a partially cleared obstacle
  for a freshly started obstacle of the same tier. Substituting a different
  obstacle while the focused one waited on its loot claim was permanently
  overwriting the bot's focus instead of only borrowing that turn.

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
