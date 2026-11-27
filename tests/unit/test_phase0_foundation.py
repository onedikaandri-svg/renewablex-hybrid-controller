"""Phase 0 foundation verification tests."""

import json
from pathlib import Path

import renewablex_controller

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_package_import_and_version() -> None:
    assert renewablex_controller.__version__ == "0.1.0"


def test_example_json_files_parse() -> None:
    plant_files = list((REPO_ROOT / "examples" / "plants").glob("*.json"))
    rule_files = list((REPO_ROOT / "examples" / "ems_rules").glob("*.json"))
    assert plant_files, "Expected at least one plant example JSON"
    assert rule_files, "Expected at least one EMS rule example JSON"

    for path in plant_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "plant_metadata" in data
        assert "control_policy" in data
        assert data["control_policy"]["hardware_commands_enabled"] is False

    for path in rule_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["ems_schema_version"] == "0.1"
        assert isinstance(data["rules"], list) and len(data["rules"]) > 0


def test_no_em_or_en_dashes_in_repo_files() -> None:
    roots = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "docs",
        REPO_ROOT / "src",
        REPO_ROOT / "examples",
        REPO_ROOT / "tests",
        REPO_ROOT / ".github",
    ]
    extensions = {".md", ".py", ".json", ".toml", ".yml"}
    files_to_check: list[Path] = []
    for root in roots:
        if root.is_file():
            files_to_check.append(root)
        elif root.is_dir():
            files_to_check.extend(
                p for p in root.rglob("*") if p.is_file() and p.suffix in extensions
            )

    offenders: list[str] = []
    for file_path in files_to_check:
        text = file_path.read_text(encoding="utf-8")
        if "\u2014" in text or "\u2013" in text:
            offenders.append(str(file_path.relative_to(REPO_ROOT)))

    assert not offenders, f"Found em/en dashes in: {offenders}"


def test_docs_and_adrs_present() -> None:
    required_docs = [
        "PROJECT_BRIEF.md",
        "ARCHITECTURE.md",
        "INTEGRATION_CONTRACT.md",
        "EMS_RULE_SCHEMA.md",
        "OPEN_ISSUES.md",
        "ROADMAP_ISSUES.md",
    ]
    for name in required_docs:
        path = REPO_ROOT / "docs" / name
        assert path.is_file() and path.stat().st_size > 0, f"Missing doc: {path}"

    adrs = sorted((REPO_ROOT / "docs" / "adr").glob("ADR-*.md"))
    assert len(adrs) == 6
    for adr in adrs:
        assert adr.stat().st_size > 0
