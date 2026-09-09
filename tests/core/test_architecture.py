from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "farm_merge_valet"
PACKAGE = "farm_merge_valet"


@dataclass(frozen=True)
class DependencyRule:
    packages: tuple[str, ...]
    forbidden: tuple[str, ...]
    description: str


CORE_OUTWARD = tuple(
    f"{PACKAGE}.{name}"
    for name in ("automation", "browser", "catalog", "cdp", "config", "gui", "observability")
)
DEPENDENCY_RULES = (
    DependencyRule(("core",), CORE_OUTWARD, "core"),
    DependencyRule(
        ("catalog",), tuple(p for p in CORE_OUTWARD if p != f"{PACKAGE}.catalog"), "catalog"
    ),
    DependencyRule(
        ("automation",),
        tuple(f"{PACKAGE}.{n}" for n in ("browser", "cdp", "composition", "gui")),
        "automation",
    ),
    DependencyRule(
        ("cdp",),
        tuple(f"{PACKAGE}.{n}" for n in ("browser", "catalog", "composition", "config", "gui")),
        "CDP adapter",
    ),
    DependencyRule(
        ("config",),
        tuple(
            f"{PACKAGE}.{n}"
            for n in (
                "automation",
                "browser",
                "catalog",
                "cdp",
                "composition",
                "gui",
                "observability",
            )
        ),
        "configuration",
    ),
    DependencyRule(
        ("browser", "observability"), (f"{PACKAGE}.composition", f"{PACKAGE}.gui"), "infrastructure"
    ),
)
RETIRED_MODULE_PREFIXES = tuple(
    f"{PACKAGE}.{name}"
    for name in (
        "core.board_scan",
        "core.bot",
        "core.catalog_builder",
        "core.catalog_labels",
        "core.catalog_store",
        "core.catalog_taxonomy",
        "core.item_catalog",
        "cdp.client",
        "cdp.scene_geometry",
        "diagnostics",
        "gui.pages._all",
        "gui.pages._configuration",
        "hotkeys",
        "logging_setup",
        "tools",
    )
)


def _module_name(path: Path) -> str:
    parts = path.relative_to(SOURCE_ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((PACKAGE, *parts))


def _resolve_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    current = _module_name(path).split(".")
    if path.name != "__init__.py":
        current.pop()
    base = current[: len(current) - (node.level - 1)]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _imports(path: Path) -> Iterable[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from(path, node)
            if module:
                yield node.lineno, module
            if node.module is None:
                yield from ((node.lineno, f"{module}.{alias.name}") for alias in node.names)


def _violations(paths: Iterable[Path], forbidden: tuple[str, ...]) -> list[str]:
    violations = []
    for path in sorted(paths):
        for line, module in _imports(path):
            if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{path.relative_to(SOURCE_ROOT)}:{line}: {module}")
    return violations


def test_package_dependency_rules() -> None:
    violations = []
    for rule in DEPENDENCY_RULES:
        paths = (p for package in rule.packages for p in (SOURCE_ROOT / package).rglob("*.py"))
        violations += [f"{rule.description}: {item}" for item in _violations(paths, rule.forbidden)]
    violations += [
        f"runtime contract: {item}"
        for item in _violations((SOURCE_ROOT / "automation" / "runtime.py",), CORE_OUTWARD[1:])
    ]
    assert not violations, "Forbidden package dependencies:\n" + "\n".join(violations)


def test_import_resolver_handles_nested_relative_imports(tmp_path: Path) -> None:
    package = tmp_path / "core" / "nested"
    package.mkdir(parents=True)
    path = package / "module.py"
    path.write_text("from ... import cdp\nfrom ...cdp import scripts\n", encoding="utf-8")
    global SOURCE_ROOT
    previous = SOURCE_ROOT
    SOURCE_ROOT = tmp_path
    try:
        assert list(_imports(path)) == [(1, PACKAGE), (1, f"{PACKAGE}.cdp"), (2, f"{PACKAGE}.cdp")]
        violations = _violations((path,), (f"{PACKAGE}.cdp",))
        assert len(violations) == 2
        assert all("core" in item and ": farm_merge_valet.cdp" in item for item in violations)
    finally:
        SOURCE_ROOT = previous


def test_retired_module_paths_are_absent_and_unreferenced() -> None:
    retired = (
        "config.py",
        "diagnostics.py",
        "hotkeys.py",
        "logging_setup.py",
        "cdp/client.py",
        "cdp/scene_geometry.py",
        "observability/discord.py",
        "tools",
        "gui/pages.py",
        "gui/pages/_all.py",
        "gui/pages/_configuration.py",
    )
    assert not [SOURCE_ROOT / path for path in retired if (SOURCE_ROOT / path).exists()]
    violations = _violations(SOURCE_ROOT.rglob("*.py"), RETIRED_MODULE_PREFIXES)
    assert not violations, "Retired imports:\n" + "\n".join(violations)


def test_configuration_has_no_global_proxy_or_settings_alias() -> None:
    from farm_merge_valet import config

    assert not hasattr(config, "Settings")
    assert not hasattr(config, "settings")
