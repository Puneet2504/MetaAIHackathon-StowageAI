"""Engine tests. Run from the backend folder:  python -m unittest discover tests"""
import json
import math
import unittest
from pathlib import Path

from app.engine.stability import TankLoad, calculate, interp, area_under, StabilityError

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
CARGO = [t for t in VESSEL["tanks"] if t["type"] == "cargo"]
BASE = [TankLoad("FOP", 300, 0.95), TankLoad("FOS", 300, 0.95), TankLoad("FW", 200, 1.0)]


class HelperTests(unittest.TestCase):
    def test_interp(self):
        self.assertAlmostEqual(interp(1.5, [1, 2], [10, 20]), 15)
        self.assertEqual(interp(0, [1, 2], [10, 20]), 10)  # clamped

    def test_area_of_straight_line(self):
        # GZ = angle in radians gives area = theta^2 / 2
        angles = [0, 30]
        gz = [0, math.radians(30)]
        self.assertAlmostEqual(area_under(angles, gz, 0, 30), math.radians(30) ** 2 / 2, places=4)


class EngineTests(unittest.TestCase):
    def test_symmetric_full_load_has_no_list_and_passes(self):
        loads = [TankLoad(t["id"], t["capacity100"] * 0.98, 0.84, "ULSD") for t in CARGO] + BASE
        r = calculate(VESSEL, loads)
        self.assertEqual(r.list_deg, 0.0)
        self.assertTrue(r.all_passed, r.criteria)

    def test_displacement_is_sum_of_weights(self):
        r = calculate(VESSEL, [], include_constants=False)
        self.assertAlmostEqual(r.displacement_t, VESSEL["lightship"]["weight"], places=1)

    def test_slack_tanks_reduce_gm(self):
        loads = [TankLoad(t["id"], t["capacity100"] * 0.5, 0.84) for t in CARGO] + BASE
        r = calculate(VESSEL, loads)
        self.assertGreater(r.free_surface_correction_m, 0)
        self.assertLess(r.gm_fluid_m, r.gm_solid_m)

    def test_one_sided_loading_lists_to_starboard_and_fails(self):
        loads = [TankLoad(t["id"], t["capacity100"] * 0.98, 0.84) for t in CARGO if t["side"] == "S"] + BASE
        r = calculate(VESSEL, loads)
        self.assertGreater(r.list_deg, 5)
        self.assertFalse(r.all_passed)

    def test_overfilled_tank_is_rejected(self):
        t = CARGO[0]
        with self.assertRaises(StabilityError):
            calculate(VESSEL, [TankLoad(t["id"], t["capacity100"] + 10, 0.84)])

    def test_unknown_tank_is_rejected(self):
        with self.assertRaises(StabilityError):
            calculate(VESSEL, [TankLoad("NOPE", 10, 0.84)])


if __name__ == "__main__":
    unittest.main()
