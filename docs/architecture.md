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
  adapter-neutral runtime contract. Workflow-specific planning, submission,
  verification, and retry state belong to collaborators under
  `automation.workflows`.
  `automation.perception` converts atomic snapshots into planning state, and
  `automation.scheduler` owns renderer-load-aware polling cadence. Workflows
  depend on the narrow internal `WorkflowContext` protocol rather than `Bot`.
  `Bot` retains lifecycle, runtime recovery, configuration adoption, scheduling,
  and loop control.
- `catalog` owns catalog models, taxonomy, labels, construction, persistence,
  blueprint mapping, asset compilation, and synchronization orchestration.
  Its synchronization service accepts resource and metadata readers; concrete
  CDP readers are supplied only by the application composition root.
- `integrations` owns registered platform identity, canonical URLs, trusted host
  and game-frame recognition, support levels, and startup strategies.
- `cdp` owns browser protocol transport, target/resource access, trusted launcher
  input and page-control primitives, atomic game-state snapshots, embedded
  scripts, profiling, and `GameRuntimeAdapter`. Target discovery walks the CDP
  parent graph from a recognized game frame to its top-level portal page, so nested
  wrappers do not leak into runtime or workflow code.
- `browser` owns managed browser process discovery, launch, shutdown, game-page
  startup, and bounded page recovery.
- `config` owns stable platform paths, validated configuration models,
  canonical hotkey values, and atomic persistence. Its package root defines
  the public configuration API.
- `gui` owns Qt pages, reusable components, services, windows, and the
  controller mediator. Qt-neutral services own cancellable background work,
  catalog onboarding and synchronization, configuration saving, hotkeys,
  native-window integration, and log export. Catalog-backed policy pages defer
  first population until selected and share one incremental-population lifecycle
  so large caches do not block initial window creation or Qt event processing.
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

The package root contains package metadata, Python/CLI entry points, shared
integration metadata, and the composition root. Feature implementation belongs
to its owning package; there is no general-purpose diagnostics module or global
settings proxy.

The public package entry points expose automation, configuration, browser,
application, and observability services. CDP modules remain internal adapters.

## Generated and user-owned data

User configuration, catalogs, and atlases use platform-specific application-data
and cache roots. Browser
profiles, captures, `.atlas_cache`, `.fmv-state`, and other runtime state are
user-owned and must not be deleted by repository maintenance. Tests and package
builds write disposable output beneath repository-local `.tmp/`. Dependencies are
resolved by the committed universal `uv.lock`; CI synchronizes it in locked mode.

## Tests

Tests follow production package boundaries. Live-game tests require explicit
opt-in, and architecture tests enforce the inward dependency rules.
