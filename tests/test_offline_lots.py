from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ies_bot_skeleton.offline.lots import fill_lots


class OfflineLotsTests(unittest.TestCase):
    def test_fill_lots_normalizes_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lot_path = base / "L42.json"
            lot_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {"kind": "wind", "id": "", "qty": "2"},
                            {"kind": "houseA"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = fill_lots(str(base), dry_run=False)
            self.assertEqual(report.files_total, 1)
            self.assertEqual(report.files_changed, 1)
            self.assertEqual(report.files_skipped, 0)

            payload = json.loads(lot_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["lot_id"], "L42")
            self.assertEqual(payload["title"], "Lot L42")
            self.assertEqual(len(payload["items"]), 2)
            self.assertEqual(payload["items"][0]["qty"], 2)
            self.assertTrue(payload["items"][0]["id"])

    def test_fill_lots_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lot_path = base / "L55.json"
            original = {"items": [{"kind": "tps", "qty": 1}]}
            lot_path.write_text(json.dumps(original), encoding="utf-8")

            report = fill_lots(str(base), dry_run=True)
            self.assertEqual(report.files_changed, 1)
            current = json.loads(lot_path.read_text(encoding="utf-8"))
            self.assertEqual(current, original)


if __name__ == "__main__":
    unittest.main()
