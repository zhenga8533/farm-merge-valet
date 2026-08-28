from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "farm_merge_valet"

FORBIDDEN_CORE_PREFIXES = (
    "farm_merge_valet.automation",
    "farm_merge_valet.browser",
    "farm_merge_valet.catalog",
    "farm_merge_valet.cdp",
    "farm_merge_valet.config",
    "farm_merge_valet.gui",
    "farm_merge_valet.observability",
)

RETIRED_MODULE_PREFIXES = (
    "farm_merge_valet.core.board_scan",
    "farm_merge_valet.core.bot",
    "farm_merge_valet.core.catalog_builder",
    "farm_merge_valet.core.catalog_labels",
    "farm_merge_valet.core.catalog_store",
    "farm_merge_valet.core.catalog_taxonomy",
    "farm_merge_valet.core.item_catalog",
    "farm_merge_valet.cdp.client",
    "farm_merge_valet.cdp.scene_geometry",
    "farm_merge_valet.diagnostics",
    "farm_merge_valet.gui.pages._all",
    "farm_merge_valet.gui.pages._configuration",
    "farm_merge_valet.hotkeys",
    "farm_merge_valet.logging_setup",
    "farm_merge_valet.tools",
)


def _imports(path: Path) -> Iterable[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.lineno, node.module


def _violations(paths: Iterable[Path], forbidden: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in sorted(paths):
        for line, module in _imports(path):
            if module.startswith(forbidden):
                violations.append(f"{path.relative_to(SOURCE_ROOT)}:{line}: {module}")
    return violations


def test_core_has_no_outward_package_dependencies() -> None:
    violations = _violations((SOURCE_ROOT / "core").glob("*.py"), FORBIDDEN_CORE_PREFIXES)
    assert not violations, "Forbidden core imports:\n" + "\n".join(violations)


def test_catalog_does_not_import_adapters() -> None:
    catalog_dir = SOURCE_ROOT / "catalog"
    adapter_prefixes = (
        "farm_merge_valet.automation",
        "farm_merge_valet.browser",
        "farm_merge_valet.cdp",
        "farm_merge_valet.config",
        "farm_merge_valet.gui",
        "farm_merge_valet.observability",
    )
    violations = _violations(catalog_dir.glob("*.py"), adapter_prefixes)
    assert not violations, "Forbidden catalog imports:\n" + "\n".join(violations)


def test_runtime_contract_is_adapter_neutral() -> None:
    forbidden = FORBIDDEN_CORE_PREFIXES[1:]
    violations = _violations((SOURCE_ROOT / "automation" / "runtime.py",), forbidden)
    assert not violations, "Forbidden runtime contract imports:\n" + "\n".join(violations)


def test_retired_module_paths_are_absent_and_unreferenced() -> None:
    retired_paths = (
        SOURCE_ROOT / "config.py",
        SOURCE_ROOT / "diagnostics.py",
        SOURCE_ROOT / "hotkeys.py",
        SOURCE_ROOT / "logging_setup.py",
        SOURCE_ROOT / "cdp" / "client.py",
        SOURCE_ROOT / "cdp" / "scene_geometry.py",
        SOURCE_ROOT / "observability" / "discord.py",
        SOURCE_ROOT / "tools",
        SOURCE_ROOT / "gui" / "pages.py",
        SOURCE_ROOT / "gui" / "pages" / "_all.py",
        SOURCE_ROOT / "gui" / "pages" / "_configuration.py",
    )
    assert not [path for path in retired_paths if path.exists()]

    violations = _violations(SOURCE_ROOT.rglob("*.py"), RETIRED_MODULE_PREFIXES)
    assert not violations, "Retired imports:\n" + "\n".join(violations)


def test_configuration_has_no_global_proxy_or_settings_alias() -> None:
    from farm_merge_valet import config

    assert not hasattr(config, "Settings")
    assert not hasattr(config, "settings")
