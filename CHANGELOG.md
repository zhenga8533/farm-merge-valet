# Changelog

## 0.2.9 - 2026-09-26

- Added a **What's new** link beside the version at the bottom of the Dashboard.
  It shows the release notes for the running version at any time, plus any newer
  releases and a download link when an update is available.
- Made the policy-page search tests independent of timing so they no longer fail
  intermittently on slow CI runners.

## 0.2.8 - 2026-09-26

- Added release notes to update notifications. The Dashboard now shows a banner
  when a newer version is available, with the notes for every release since
  yours, a download link, and an option to skip that version.
- Fixed startup update checks never finding a release. The repository is now
  public, and an unreachable release feed is recorded as a failed check in the
  diagnostic log instead of being treated as "no update".
- Releases now run the full CI workflow before publishing and include a
  `SHA256SUMS.txt` file for verifying downloads.
- CI now also runs on Python 3.14 and builds the Windows executable on every
  run.
- Consolidated the documentation: runtime health and recovery are covered in
  one document, and event islands are now documented.

## 0.2.7 - 2026-09-25

- Fixed the bot waiting indefinitely on an "unsupported game overlay" when an
  interrupted collect animation left transparent effects in the popup layer.

## 0.2.6 - 2026-09-24

- Fixed event island visits stopping after the game page reloads. A freshly
  loaded page keeps showing its saved event energy and does not regenerate it
  until the island is opened, so the energy threshold was never reached. A new
  "Energy check visit" setting (default 60 minutes, 0 to disable) visits the
  island below the threshold when it has not been visited since the bot started
  or within that interval.
- Made the CI dependency audit tolerate slow PyPI responses instead of failing
  on a 15-second read timeout.
- Updated ruff to 0.16.8.

## 0.2.5 - 2026-09-21

- Fixed the bot repeatedly retrying a reward container the game reported as busy
  (for example while its reward animation is paused because the game window is in
  the background). The busy target is now deferred so other work can proceed, and
  a warning is logged if the game stays busy for two minutes.
- Fixed the CLI and GUI showing a stale version (such as 0.1.1) when running from
  a source checkout with an older editable install.
- Release notes on GitHub now start with the version's CHANGELOG entry.

## 0.2.4 - 2026-09-21

- Fixed the bot freezing on an "unsupported overlay" when a toast notification
  (such as "Visited your farm") was queued in front of a supported popup. Toasts
  are now recognized by structure and skipped, so the popup behind them (for
  example a timed event announcement) is dismissed normally.
- Added a warning when an unsupported overlay has blocked automation for two
  minutes, so an unattended bot no longer stalls silently.

## 0.2.3 - 2026-09-19

- Added a cooldown for a revoked or deleted Discord webhook so it stops
  retrying and re-logging the same failure on every status refresh.
- Fixed catalog sync progress and errors being lost to bare console output
  instead of the diagnostic log, and Windows DPAPI failures going unlogged.
- Fixed config loading crashing entirely when the stored Discord webhook
  secret was unreadable; it now falls back to treating it as unset.
- Fixed force-lucky-merge attempts never taking part in the shared action
  lease, which could leak stale retry state and mark an attempt complete
  even when it was only interrupted by a shutdown.
- Renamed several cross-module automation methods that were called across
  class boundaries despite their leading underscore, and shared the
  duplicated onboarding/loading scaffolding across the Buildings, Items,
  Marketplace, and Shops pages.

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
