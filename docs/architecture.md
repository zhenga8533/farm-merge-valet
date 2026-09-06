# Architecture

Farm Merge Valet is organized around a pure domain with explicit feature and
adapter boundaries. Imports point inward: entry points and GUI code compose
features, automation consumes protocols and domain models, and adapters
implement those protocols.

```text
GUI / CLI -> automation and catalog services -> core
                     ^
               CDP adapters
```

## Package ownership

- `core` owns item identities, board state, merge planning, shop and marketplace planning, and
  upgrade-progress value objects. It performs no I/O and imports no GUI,
  browser, CDP, configuration-persistence, or observability code.
- `automation` owns the bot lifecycle, action-workflow execution, and its
  adapter-neutral runtime contract. `Bot` receives an `AppConfig` snapshot, a
  runtime, and a catalog provider. Saved updates are handed to a running bot as
  complete snapshots and adopted between planning iterations. Workflow-specific
  planning, submission, pending-action verification, retry state, and execution
  belong to dedicated merge, tile-interaction, storage-bubble, supply-crate,
  shop, and marketplace objects under `automation.workflows`.
  `automation.perception` converts atomic snapshots into planning state, and
  `automation.scheduler` owns renderer-load-aware polling cadence. Marketplace purchases use
  stable `flash:<slot>:<candidate>` or `free:<offer>` identities.
- `catalog` owns catalog models, taxonomy, labels, construction, persistence,
  blueprint mapping, asset compilation, and synchronization orchestration.
  Its synchronization service accepts resource and metadata readers; concrete
  CDP readers are supplied only by the application composition root.
- `cdp` owns browser protocol transport, target/resource access, trusted launcher
  input and page-control primitives, atomic game-state snapshots, embedded
  scripts, profiling, and `GameRuntimeAdapter`.
- `browser` owns managed browser process discovery, launch, shutdown, game-page
  startup, and bounded page recovery.
- `config` owns stable platform paths, validated schema-version-1 models,
  canonical hotkey values, and atomic persistence. Its package root defines
  the public configuration API.
- `gui` owns Qt pages, reusable components, services, windows, and the
  controller mediator. Qt-neutral services own cancellable background work,
  catalog onboarding and synchronization, configuration saving, hotkeys,
  native-window integration, and log export.
- `observability` owns logging and Discord event delivery. Discord queue
  orchestration, status reduction/payloads, and webhook transport/persisted
  message identity are separate modules.

## Composition roots and dependency rules

`composition.py` is the application composition root. It constructs the CDP
runtime and live/cache catalog provider and injects them into `Bot`. GUI and CLI
entry points may import adapters and feature services; feature services must not
construct adapters internally. CDP translates raw game data to models from
`automation.runtime`.

The automation hot path crosses the adapter boundary once per iteration through
`RuntimeSnapshot`. Optional sections are selected from enabled policy and
pending work, so feature-specific reads are not separate renderer requests.
Marketplace state is included only while a marketplace policy is enabled or a
purchase is pending. The GUI owns only the persistent catalog and policy editor;
all live marketplace reads belong to the automation runtime.

The package root contains only package metadata, Python/CLI entry points, and
the shared composition root. Feature implementation belongs to its owning
package; there is no general-purpose diagnostics module or global settings
proxy.

The public package entry points are `farm_merge_valet.automation`,
`farm_merge_valet.config`, `farm_merge_valet.browser`,
`farm_merge_valet.gui.run_application`, and
`farm_merge_valet.observability.discord`. CDP modules are internal adapters.
Former private paths under `core.catalog_*`, `core.bot`, `tools`, and the old
top-level logging module are intentionally not retained.

## Generated and user-owned data

User configuration remains schema version 1 at the existing platform-specific
data path. Catalogs and atlases remain under the existing cache root. Browser
profiles, captures, `.atlas_cache`, `.fmv-state`, and other runtime state are
user-owned and must not be deleted by repository maintenance. Tests and package
builds write disposable output beneath repository-local `.tmp/`.

## Tests

Tests mirror production packages under `tests/automation`, `tests/catalog`,
`tests/config`, `tests/core`, `tests/cdp`, `tests/gui`, `tests/browser`, and
`tests/observability`. Live integration tests belong in `tests/integration`
once an explicitly marked live test exists. Architecture tests parse imports,
enforce inward dependencies, and reject retired module paths so old structure
cannot return unnoticed.
