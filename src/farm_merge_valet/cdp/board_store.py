"""Read the game's own live board/cell state directly from its in-memory
data store, instead of inferring it from screen captures.

The board has no accessible persisted copy (`localStorage`/`IndexedDB`/
Cache Storage all confirmed empty for this origin; the only real service
worker on the page is Reddit's own, unrelated to the game), so the
authoritative state only ever exists as live objects in the page's JS
memory. The class holding it was identified by searching the game's
minified webpack module graph for a board/cell-manager method signature,
then confirmed live (via a debugger breakpoint hit on a real merge) to be
the one actually mutated by real gameplay.

Rather than depending on a debugger breakpoint at runtime, `arm_board_store`
monkey-patches that class's `setContent` directly via a plain
`Runtime.evaluate`, so the *first* real call -- which happens automatically
as the game populates the board from saved data, no gameplay action
required -- stashes a live reference at `window.__fmvBoardStore`. Patching
the prototype (not an instance) also works retroactively on an
already-running game, since existing instances share it.

The class is found by method signature every call, not a cached module ID
-- bundle filenames are content-hashed, so a redeploy can renumber every
module. Not bulletproof (a redeploy could also rename the methods
themselves), which is why `arm_board_store` fails soft -- returns a status
string, never raises -- so that kind of breakage is diagnosable from logs
and falls back to vision (see `core/bot.py`) instead of crashing.
"""

from __future__ import annotations

from farm_merge_valet.cdp.client import evaluate
from farm_merge_valet.core.board import GridCoord

# Method names that, together, uniquely identified the board/cell-store
# class among every module in the game's bundle (~1200 modules, checked
# live) -- see module docstring. Re-searched every `arm_board_store` call
# rather than caching a module ID, so this survives the game's bundle
# being renumbered by a redeploy.
_CELL_STORE_SIGNATURE = [
    "setContent",
    "getContent",
    "addCell",
    "hasCell",
    "_onCellContentAdded",
    "_onCellContentRemoved",
    "connectCellToNeighbours",
]

_ARM_SCRIPT = f"""
(() => {{
  if (window.__fmvBoardStore) return 'already-captured';
  if (window.__fmvBoardStorePatched) return 'already-patched';
  const chunkArr = window.webpackChunkfarm_merge_game;
  if (!chunkArr) return 'no-chunk-array';
  let req;
  try {{
    chunkArr.push([[Symbol()], {{}}, (r) => {{ req = r; }}]);
  }} catch (e) {{ return 'push-failed: ' + e; }}
  if (!req || !req.m) return 'no-require';

  const REQUIRED = {_CELL_STORE_SIGNATURE!r};
  let target = null;
  for (const id of Object.keys(req.m)) {{
    let exp;
    try {{ exp = req(id); }} catch (e) {{ continue; }}
    if (!exp || typeof exp !== 'object') continue;
    for (const key of Object.keys(exp)) {{
      const candidate = exp[key];
      if (typeof candidate !== 'function' || !candidate.prototype) continue;
      let protoKeys;
      try {{
        protoKeys = Object.getOwnPropertyNames(candidate.prototype);
      }} catch (e) {{ continue; }}
      if (REQUIRED.every((m) => protoKeys.includes(m))) {{
        target = candidate;
        break;
      }}
    }}
    if (target) break;
  }}
  if (!target) return 'class-not-found';

  const proto = target.prototype;
  const orig = proto.setContent;
  proto.setContent = function(...args) {{
    if (!window.__fmvBoardStore) window.__fmvBoardStore = this;
    return orig.apply(this, args);
  }};
  window.__fmvBoardStorePatched = true;
  return 'patched';
}})()
"""

_READ_SCRIPT = """
(() => {
  const store = window.__fmvBoardStore;
  if (!store || !store._cells) return null;
  const out = [];
  for (const cell of store._cells.values()) {
    out.push({
      column: cell.column,
      row: cell.row,
      blueprintID: cell._content ? cell._content._blueprintID : null,
    });
  }
  return out;
})()
"""


def arm_board_store(port: int) -> str:
    """Best-effort, idempotent: patch the game's board-store class so the
    next real `setContent` call (naturally triggered by the game itself,
    no gameplay action needed -- see module docstring) stashes a live
    reference for `read_board_state` to use. Safe to call every step;
    cheap once already armed/captured (a couple of property checks).

    Returns a short status string for logging (`"patched"`,
    `"already-captured"`, `"module-not-loaded"`, etc.) rather than a
    bool/None, so a game update that breaks this (see module docstring)
    is diagnosable from logs instead of silently doing nothing.
    """
    result = evaluate(port, _ARM_SCRIPT)
    return str(result) if result is not None else "no-result"


def read_board_state(port: int) -> dict[GridCoord, str] | None:
    """Every cell's current content, straight from the game's own live
    data -- `{(column, row): blueprintID}`. `blueprintID` is the game's
    own item-identifier string (e.g. `"wheat_1"`, matching
    `<item name>_<tier>`; see `core/board_scan.discover_blueprint_items`),
    or a non-item marker like `"empty"` (genuinely empty farmland),
    `"area_cloud"`/`"premium_cloud"` (locked/premium), or a building's own
    ID (e.g. `"bakery"`) for fixed decoration.

    Cells the game has never called `setContent` on at all (no data
    either way -- confirmed live, ~3% of cells even on a well-progressed
    account) are omitted rather than guessed at.

    Returns None if `arm_board_store` hasn't successfully captured a
    reference yet.
    """
    raw = evaluate(port, _READ_SCRIPT)
    if not isinstance(raw, list):
        return None
    return {
        (entry["column"], entry["row"]): entry["blueprintID"]
        for entry in raw
        if entry.get("blueprintID") is not None
    }
