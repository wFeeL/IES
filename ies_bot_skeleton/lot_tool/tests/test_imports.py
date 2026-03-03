import unittest


class ImportTests(unittest.TestCase):
    def test_imports(self) -> None:
        import sys
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

        import lottool.cli  # noqa: F401


if __name__ == "__main__":
    unittest.main()
