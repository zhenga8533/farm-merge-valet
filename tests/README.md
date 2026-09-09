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
- `integration/`: explicitly opted-in checks against the configured live game

Root-level tests cover application entry points and public package exports. Shared,
automatically applied test isolation belongs in the root `conftest.py`; narrowly
scoped fixtures should live in the closest relevant test directory.

The default suite remains deterministic and does not require a running browser or
game. Live tests are skipped unless `--live-game` is supplied. They use the real
platform configuration rather than the suite's temporary configuration, require the
managed browser and game to already be running, and perform read-only runtime checks:

```powershell
uv run --locked pytest tests/integration --live-game
```

Tests marked `live_action` additionally require `--live-actions`. They submit real
game actions and must only be run against an account where that mutation is intended:

```powershell
uv run --locked pytest tests/integration --live-game --live-actions
```
