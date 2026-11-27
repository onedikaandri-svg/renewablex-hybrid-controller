"""AST boundary tests guaranteeing pure-Python core isolation and no eval/exec."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "src" / "renewablex_controller" / "core"
SRC_DIR = REPO_ROOT / "src" / "renewablex_controller"

_FORBIDDEN_CORE_IMPORTS = {
    "renewablex_controller.devices",
    "renewablex_controller.adapters",
    "renewablex_controller.edge_api",
    "fastapi",
    "uvicorn",
    "sqlite3",
    "pymodbus",
    "opcua",
    "paho",
}


def test_core_has_zero_edge_protocol_web_or_db_imports() -> None:
    violations: list[str] = []
    for py_file in sorted(CORE_DIR.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in _FORBIDDEN_CORE_IMPORTS:
                        if alias.name == forbidden or alias.name.startswith(f"{forbidden}."):
                            violations.append(f"{py_file.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in _FORBIDDEN_CORE_IMPORTS:
                    if node.module == forbidden or node.module.startswith(f"{forbidden}."):
                        violations.append(f"{py_file.name}: from {node.module}")
    assert not violations, f"Forbidden imports in core/: {violations}"


def test_src_contains_no_eval_exec_or_compile_calls() -> None:
    forbidden_calls = {"eval", "exec", "compile"}
    offenders: list[str] = []
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in forbidden_calls
            ):
                offenders.append(f"{py_file.name}:{node.lineno} calls {node.func.id}()")
    assert not offenders, f"Forbidden dynamic code calls found: {offenders}"
