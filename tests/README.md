# Test suite

Tests are grouped by the production boundary they exercise:

- `browser/`: managed-browser lifecycle and compatibility
- `cdp/`: Chrome DevTools Protocol clients, runtime adapters, and live-state readers
- `automation/`: bot lifecycle, workflows, and adapter-neutral runtime contracts
- `catalog/`: catalog construction, persistence, synchronization, and asset compilation
- `config/`: configuration models, platform paths, and persistence
- `core/`: pure board, item, merge, shop, and upgrade-progress domain models
- `gui/`: desktop UI behavior
- `observability/`: logging and Discord delivery

Root-level tests cover application entry points and public package exports. Shared,
automatically applied test isolation belongs in the root `conftest.py`; narrowly
scoped fixtures should live in the closest relevant test directory.

The default suite must remain deterministic and must not require a running browser or
game. Put future external integration tests in `tests/integration/` and explicitly mark
live acceptance tests so a normal `pytest` run cannot operate the game.
