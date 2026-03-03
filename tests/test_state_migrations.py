from __future__ import annotations

import json
import os
import tempfile
import unittest

from ies_bot_skeleton.online.state import load_state, save_state


class StateMigrationTests(unittest.TestCase):
    def test_load_state_migrates_v1_to_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with open("state.json", "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "wind_k": {"W1": 0.1},
                            "solar": {"best_angle": {}, "best_power": {}},
                            "last_solar_angle": {},
                            "printed_once": False,
                        },
                        f,
                    )

                st = load_state(season="2026")
                self.assertEqual(st.schema_version, 2)
                self.assertEqual(st.season, "2026")
                self.assertTrue(st.created_at)
                self.assertTrue(st.updated_at)

                with open("state.json", "r", encoding="utf-8") as f:
                    payload = json.load(f)
                self.assertEqual(payload["schema_version"], 2)
            finally:
                os.chdir(old_cwd)

    def test_save_state_writes_atomic_schema_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                st = load_state(season="default")
                st.wind_k["W1"] = 0.2
                save_state(st)
                with open("state.json", "r", encoding="utf-8") as f:
                    payload = json.load(f)
                self.assertEqual(payload["schema_version"], 2)
                self.assertIn("updated_at", payload)
                self.assertEqual(payload["wind_k"]["W1"], 0.2)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
