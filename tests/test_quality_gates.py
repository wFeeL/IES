from __future__ import annotations

import unittest
from pathlib import Path

from ies_bot_skeleton.common.forecast_loader import lookup_pack_value


def _should_skip_python_path(path: Path, excluded_dirs: set[str]) -> bool:
    if any(part in excluded_dirs for part in path.parts):
        return True
    if any(part.endswith(".egg-info") for part in path.parts):
        return True
    if path.name.startswith("._"):
        return True
    return False


class QualityGateTests(unittest.TestCase):
    def test_python_sources_have_valid_syntax(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        excluded_dirs = {
            ".git",
            ".venv",
            "dist",
            "build",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
        }

        errors: list[str] = []
        for path in repo_root.rglob("*.py"):
            if _should_skip_python_path(path, excluded_dirs):
                continue

            source = path.read_text(encoding="utf-8", errors="ignore")
            try:
                compile(source, str(path), "exec")
            except SyntaxError as exc:
                line = exc.lineno or 0
                col = exc.offset or 0
                errors.append(f"{path}:{line}:{col}: {exc.msg}")

        if errors:
            self.fail("Syntax errors found:\n" + "\n".join(errors))

    def test_forecast_loader_basic(self) -> None:
        pack = {"wind": {"W1": {0: 3.5, 1: 4.0}}}
        self.assertEqual(lookup_pack_value(pack, "wind", ("W1",), 0, default=0.0), 3.5)
        self.assertEqual(lookup_pack_value(pack, "wind", ("w1",), 1, default=0.0), 4.0)
        self.assertEqual(lookup_pack_value(pack, "wind", ("MISSING",), 1, default=7.0), 7.0)

    def test_quality_gate_ignores_macos_metadata_python_files(self) -> None:
        excluded_dirs = {
            ".git",
            ".venv",
            "dist",
            "build",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
        }
        path = Path("/tmp/project/tests/._broken.py")
        self.assertTrue(_should_skip_python_path(path, excluded_dirs))


if __name__ == "__main__":
    unittest.main()
