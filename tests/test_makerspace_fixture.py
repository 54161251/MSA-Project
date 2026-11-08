import json
import unittest
from pathlib import Path


class MakerspaceFixtureTest(unittest.TestCase):
    def test_each_floor_plan_marker_resolves_to_one_equipment_record(self):
        fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "makerspace.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        equipment_ids = [equipment["id"] for equipment in fixture["equipment"]]
        markers = fixture["floor_plan"]["markers"]

        self.assertTrue(markers, "The demo floor plan needs equipment markers")
        for marker in markers:
            equipment_id = marker["equipment_id"]
            with self.subTest(equipment_id=equipment_id):
                self.assertEqual(equipment_ids.count(equipment_id), 1)
