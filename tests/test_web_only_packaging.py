from __future__ import annotations

from pathlib import Path
import tomllib

from ies_bot_skeleton.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATHS = [
    ROOT / "ies_bot_skeleton" / "cli.py",
    ROOT / "ies_bot_skeleton" / "entrypoints.py",
    ROOT / "ies_bot_skeleton" / "online",
    ROOT / "ies_bot_skeleton" / "offline",
    ROOT / "ies_bot_skeleton" / "lot_tool",
    ROOT / "ies_bot_skeleton" / "compat",
    ROOT / "ies.py",
    ROOT / "main.py",
]


def test_create_app_smoke_and_flask_cli_registration():
    app = create_app("testing")
    runner = app.test_cli_runner()

    result = runner.invoke(args=["--help"])

    assert result.exit_code == 0
    assert "seed" in result.output
    assert "create-admin" in result.output
    assert "import-legacy" in result.output
    assert "ies-online" not in result.output
    assert "ies-lottool" not in result.output
    assert "ies-fill-lots" not in result.output


def test_pyproject_is_web_only():
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload["project"]

    assert "web application" in project["description"].lower()
    assert "scripts" not in project or not project["scripts"]
    assert payload["tool"]["black"]["target-version"] == ["py311"]
    assert payload["tool"]["ruff"]["target-version"] == "py311"
    assert payload["tool"]["mypy"]["python_version"] == "3.11"


def test_legacy_runtime_paths_removed():
    missing = [path for path in LEGACY_PATHS if path.exists()]
    assert missing == []


def test_gitignore_excludes_macos_appledouble_artifacts():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "._*" in gitignore
    assert "__MACOSX/" in gitignore
