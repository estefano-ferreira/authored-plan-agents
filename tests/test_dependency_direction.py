"""Checks dependency direction between layers via AST (see DESIGN.md, Layers and dependencies section).

- core: imports only stdlib.
- ai: internally imports only core (+ stdlib and `yaml`).
- infrastructure: free (core, ai, stdlib, any 3rd-party) — only needs to be parseable.
"""
import ast
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
_STDLIB = set(sys.stdlib_module_names) | set(sys.builtin_module_names)


def _iter_py_files(package_dir: Path) -> list[Path]:
    return sorted(package_dir.rglob("*.py"))


def _top_level(module_name: str) -> str:
    return module_name.split(".")[0]


def _imported_top_level_modules(path: Path) -> set[str]:
    """Top-level names of all modules imported (absolute) in `path`; relative imports are ignored."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(_top_level(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import: always internal to its own package, violates no direction
            if node.module:
                modules.add(_top_level(node.module))
    return modules


def test_core_imports_only_stdlib():
    """core/ cannot import `ai`, `infrastructure` or any third-party dependency."""
    violations = []
    for path in _iter_py_files(_SRC_DIR / "core"):
        for mod in _imported_top_level_modules(path):
            if mod == "core":
                continue
            if mod not in _STDLIB:
                violations.append(f"{path.relative_to(_SRC_DIR)}: forbidden import '{mod}' (core can only use stdlib)")
    assert not violations, "dependency violations in core:\n" + "\n".join(violations)


def test_ai_imports_only_core_and_allowed_third_party():
    """ai/ can only import `core`, stdlib and `yaml` (the only third-party exception, for declarative loading)."""
    allowed_third_party = {"yaml"}
    violations = []
    for path in _iter_py_files(_SRC_DIR / "ai"):
        for mod in _imported_top_level_modules(path):
            if mod in {"ai", "core"} or mod in allowed_third_party or mod in _STDLIB:
                continue
            violations.append(f"{path.relative_to(_SRC_DIR)}: forbidden import '{mod}' (ai can only use core/stdlib/yaml)")
    assert not violations, "dependency violations in ai:\n" + "\n".join(violations)


def test_infrastructure_files_are_well_formed():
    """infrastructure/ is free (core, ai, stdlib, any 3rd-party): we only guarantee the files parse."""
    violations = []
    for path in _iter_py_files(_SRC_DIR / "infrastructure"):
        try:
            _imported_top_level_modules(path)
        except SyntaxError as exc:
            violations.append(f"{path.relative_to(_SRC_DIR)}: syntax error ({exc})")
    assert not violations, "malformed files in infrastructure:\n" + "\n".join(violations)
